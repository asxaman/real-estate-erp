// Property form: show Owner, Fee and agreement dates only for third-party properties.
(function () {
  "use strict";

  function init() {
    var select = document.getElementById("id_ownership_type");
    if (!select) {
      return;
    }

    var rows = document.querySelectorAll(
      ".form-row.field-owner, " +
        ".form-row.field-management_fee_percent, " +
        ".form-row.field-management_start, " +
        ".form-row.field-management_end"
    );

    function update() {
      var thirdParty = select.value === "third_party";

      rows.forEach(function (row) {
        row.style.display = thirdParty ? "" : "none";
      });

      // A company-owned property has no fee or agreement. Clear the boxes so an
      // old value (such as 0) is not carried over if you switch back.
      if (!thirdParty) {
        ["id_management_fee_percent", "id_management_start", "id_management_end"].forEach(
          function (id) {
            var input = document.getElementById(id);
            if (input) {
              input.value = "";
            }
          }
        );
      }
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
