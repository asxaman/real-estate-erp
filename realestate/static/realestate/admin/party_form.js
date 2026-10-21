// Owner and tenant forms: show the company-only fields only for companies.
(function () {
  "use strict";

  function init() {
    var select =
      document.getElementById("id_tenant_type") ||
      document.getElementById("id_owner_type");
    if (!select) {
      return;
    }

    var rows = document.querySelectorAll(
      ".form-row.field-trade_licence_number, " +
        ".form-row.field-trade_licence_expiry, " +
        ".form-row.field-trn, " +
        ".form-row.field-authorised_signatory"
    );

    function update() {
      var company = select.value === "company";
      rows.forEach(function (row) {
        row.style.display = company ? "" : "none";
      });
    }

    select.addEventListener("change", update);
    update();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
