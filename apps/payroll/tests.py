from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.contacts.models import Customer
from apps.payroll.models import Employee


class EmployeeListSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="payrolltest", password="x")
        self.client = Client()
        self.client.force_login(self.user)
        self.employee = Employee.objects.create(name_ar="أحمد", net_balance=Decimal("10"))

    def test_employee_list_search_by_name(self):
        url = reverse("shell:employees")
        response = self.client.get(url, {"q": "أحمد", "hide_zero_net": "0"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أحمد")

    def test_employee_list_search_by_linked_customer_phone(self):
        customer = Customer.objects.create(name_ar="عميل", phone="0599123456")
        self.employee.linked_customer = customer
        self.employee.save(update_fields=["linked_customer"])
        url = reverse("shell:employees")
        response = self.client.get(url, {"q": "0599", "hide_zero_net": "0"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أحمد")
