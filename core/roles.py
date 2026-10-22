"""Standard roles (Django groups). Module permissions are attached to these as modules are built."""

PROPERTY_MANAGER = "Property Manager"
LEASING_AGENT = "Leasing Agent"
ACCOUNTANT = "Accountant"
MAINTENANCE_SUPERVISOR = "Maintenance Supervisor"
CUSTOMER_SERVICE = "Customer Service"
TENANT = "Tenant"  # portal users (Module: Portals)
OWNER = "Owner"  # portal users (Module: Portals)

ROLES = [
    PROPERTY_MANAGER,
    LEASING_AGENT,
    ACCOUNTANT,
    MAINTENANCE_SUPERVISOR,
    CUSTOMER_SERVICE,
    TENANT,
    OWNER,
]
