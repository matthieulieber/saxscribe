import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.saxscribe import billing
from backend.saxscribe.settings import settings


class FakePrice:
    @staticmethod
    def retrieve(price_id):
        return SimpleNamespace(id=price_id, unit_amount=700, currency="usd", active=True, recurring=None)


class FakeSessionAPI:
    session = None
    created = None

    @classmethod
    def create(cls, **kwargs):
        cls.created = kwargs
        return SimpleNamespace(id="cs_test_created", url="https://checkout.stripe.test/session")

    @classmethod
    def retrieve(cls, session_id, expand=None):
        return cls.session


class FakeStripe:
    Price = FakePrice
    checkout = SimpleNamespace(Session=FakeSessionAPI)


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            "runtime_mode": settings.runtime_mode,
            "uvr_model_dir": settings.uvr_model_dir,
            "public_base_url": settings.public_base_url,
            "stripe_secret_key": settings.stripe_secret_key,
            "stripe_webhook_secret": settings.stripe_webhook_secret,
            "stripe_enhanced_price_id": settings.stripe_enhanced_price_id,
            "lalal_api_key": settings.lalal_api_key,
        }
        self.environment = mock.patch.dict("os.environ", {"OPENAI_API_KEY": "openai-test"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        for name, value in self.previous.items():
            object.__setattr__(settings, name, value)

    def _hosted(self, model_dir):
        object.__setattr__(settings, "runtime_mode", "gcp")
        object.__setattr__(settings, "uvr_model_dir", str(model_dir))
        object.__setattr__(settings, "public_base_url", "https://saxscribe.example")
        object.__setattr__(settings, "stripe_secret_key", "sk_test_example")
        object.__setattr__(settings, "stripe_enhanced_price_id", "price_enhanced")
        object.__setattr__(settings, "lalal_api_key", "lalal-test")

    def test_local_config_exposes_only_free_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / settings.uvr_model_name
            model.write_bytes(b"model")
            object.__setattr__(settings, "runtime_mode", "local")
            object.__setattr__(settings, "uvr_model_dir", directory)
            config = billing.public_billing_config()
        self.assertTrue(config["free"]["available"])
        self.assertFalse(config["enhanced"]["available"])
        self.assertFalse(config["free"]["ai_review"])

    def test_checkout_uses_one_configured_price_and_return_token(self):
        with tempfile.TemporaryDirectory() as directory:
            self._hosted(directory)
            with mock.patch("backend.saxscribe.billing._stripe", return_value=FakeStripe):
                result = billing.create_enhanced_checkout()
        self.assertEqual(result["session_id"], "cs_test_created")
        self.assertEqual(FakeSessionAPI.created["mode"], "payment")
        self.assertEqual(FakeSessionAPI.created["line_items"], [{"price": "price_enhanced", "quantity": 1}])
        self.assertIn("{CHECKOUT_SESSION_ID}", FakeSessionAPI.created["success_url"])

    def test_paid_session_must_match_plan_and_price(self):
        with tempfile.TemporaryDirectory() as directory:
            self._hosted(directory)
            FakeSessionAPI.session = SimpleNamespace(
                id="cs_test_paid",
                mode="payment",
                status="complete",
                payment_status="paid",
                metadata={"saxscribe_plan": "enhanced"},
                line_items={"data": [{"price": {"id": "price_enhanced"}}]},
                amount_total=700,
                currency="usd",
                payment_intent="pi_test",
                customer_details={"email": "player@example.com"},
            )
            with mock.patch("backend.saxscribe.billing._stripe", return_value=FakeStripe):
                paid = billing.verify_paid_enhanced_checkout("cs_test_paid")
                FakeSessionAPI.session.payment_status = "unpaid"
                with self.assertRaisesRegex(billing.BillingError, "not complete"):
                    billing.verify_paid_enhanced_checkout("cs_test_paid")
                FakeSessionAPI.session.payment_status = "paid"
                FakeSessionAPI.session.line_items = {"data": [{"price": {"id": "price_other"}}]}
                with self.assertRaisesRegex(billing.BillingError, "does not match"):
                    billing.verify_paid_enhanced_checkout("cs_test_paid")
        self.assertEqual(paid.amount_total, 700)
        self.assertEqual(paid.customer_email, "player@example.com")


if __name__ == "__main__":
    unittest.main()
