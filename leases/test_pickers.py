from django.contrib.auth import get_user_model
from django.urls import reverse

from .tests import Setup, make_tenant


class PickerSearch(Setup):
    """Update 41: the lease form's unit / tenant / property search accepts the reference and the chosen label."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("pickboss", "pk@example.com", "Pass-word1!"))

    def labels(self, kind, text):
        reply = self.client.get(reverse("admin:leases_lease_search"), {"kind": kind, "q": text})
        return [r["label"] for r in reply.json()["results"]]

    def test_a_unit_is_found_by_its_full_reference_in_any_style(self):
        code = self.flat.property.code
        number = self.flat.unit_number
        for text in (f"{code}-{number}", f"{code} {number}", f"{code}{number}".lower(), f"{number} {code}"):
            self.assertTrue(any(f"{code}-{number}" in label for label in self.labels("unit", text)), text)

    def test_the_whole_chosen_label_finds_the_unit_again(self):
        from .pickers import unit_label

        self.assertEqual(len(self.labels("unit", unit_label(self.flat))), 1)

    def test_a_tenant_is_found_by_name_words_in_any_order_and_by_phone(self):
        make_tenant("Ahmed Abbas", phone="+971 50 123 4567")
        self.assertEqual(self.labels("tenant", "abbas ahmed"), ["Ahmed Abbas"])
        self.assertEqual(self.labels("tenant", "050 123 4567"), ["Ahmed Abbas"])
