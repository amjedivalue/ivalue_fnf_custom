const CHILD_TABLE_DOCTYPE = "Full and Final Outstanding Statement";
let isAutoFilling = false; // prevents loops during set_value + table fill

frappe.ui.form.on("Full and Final Statement", {
  onload(frm) {
    toggle_sections(frm, Boolean(frm.doc.employee), false);

    if (!frm.doc.transaction_date) {
      frm.set_value("transaction_date", frappe.datetime.get_today());
    }

    if (frm.doc.employee) {
      recalculate_full_and_final(frm);
    }
  },

  async employee(frm) {
    toggle_sections(frm, Boolean(frm.doc.employee), true);
/**
 * Toggle sections and recalculate full and final statement when employee is changed
 * @param {frappe.ui.Form} frm - the form object
 * @returns {Promise<void>}
 */

    if (!frm.doc.employee) {
      clear_all_fields(frm);
      return;
    }

    await recalculate_full_and_final(frm);
  },

  async transaction_date(frm) {
    if (!frm.doc.employee) return;
    await recalculate_full_and_final(frm);
  },

  async custom_recalculate(frm) {
    if (!frm.doc.employee) return;
    await recalculate_full_and_final(frm);
  },
});

async function recalculate_full_and_final(frm) {
  if (isAutoFilling) return;
  isAutoFilling = true;

  try {
    const response = await frm.call({
      method: "ivalue_fnf_custom.api.full_and_final.get_full_and_final_payload",
      args: {
        employee: frm.doc.employee,
        transaction_date: frm.doc.transaction_date,
      },
    });

    const payload = response.message;

    if (!payload || !payload.ok) {
      frappe.msgprint(payload?.msg || "Unable to calculate Full & Final.");
      return;
    }

    if (payload.note) {
      frappe.msgprint({
        title: "Note",
        message: payload.note,
        indicator: "orange",
      });
    }

    // ===== Service fields
    await frm.set_value("custom_service_years", payload.service?.years || 0);
    await frm.set_value("custom_service_month", payload.service?.months || 0);
    await frm.set_value("custom_service_days", payload.service?.days || 0);
    await frm.set_value("custom_total_of_years", payload.service?.custom_total_of_years || 0);

    // ===== Totals
    await frm.set_value("total_payable_amount", flt(payload.totals?.total_payable || 0));

    // ===== Payables child table
    frm.clear_table("payables");

    (payload.payables || []).forEach((serverRow) => {
      const childRow = frm.add_child("payables");

      childRow.component = serverRow.component || "";
      childRow.day_count = flt(serverRow.day_count ?? serverRow.worked_days ?? 0);
      childRow.amount = flt(serverRow.amount ?? serverRow.auto_amount ?? 0);

      childRow.reference_document_type = serverRow.reference_document_type || "";
      childRow.reference_document = serverRow.reference_document || "";

      // optional columns (only if present in your child doctype)
      if (childRow.rate_per_day !== undefined) {
        childRow.rate_per_day = flt(serverRow.rate_per_day || 0);
      }
      if (childRow.worked_days !== undefined) {
        childRow.worked_days = flt(serverRow.worked_days || 0);
      }
      if (childRow.auto_amount !== undefined) {
        childRow.auto_amount = flt(serverRow.auto_amount || serverRow.amount || 0);
      }
    });

    frm.refresh_field("payables");

    // re-check total from table
    update_total_from_table(frm);
  } finally {
    isAutoFilling = false;
  }
}

function clear_all_fields(frm) {
  frm.clear_table("payables");
  frm.refresh_field("payables");

  frm.set_value({
    custom_service_years: 0,
    custom_service_month: 0,
    custom_service_days: 0,
    custom_total_of_years: 0,
    total_payable_amount: 0,
  });
}

function toggle_sections(frm, shouldShow, animate = true) {
  const sectionFieldnames = [
    "custom_service_detalies",
    "section_break_8",
    "section_break_10",
    "section_break_15",
    "totals_section",
    "employee_details_section",
    "custom_final_settlement_section",
  ];

  sectionFieldnames.forEach((fieldname) => {
    const field = frm.get_field(fieldname);
    if (!field) return;

    const wrapper = $(field.wrapper);
    if (shouldShow) {
      animate ? wrapper.stop().slideDown(400) : wrapper.show();
    } else {
      animate ? wrapper.stop().slideUp(400) : wrapper.hide();
    }
  });
}

// =========================================================
// Manual edits in child table
// =========================================================
frappe.ui.form.on(CHILD_TABLE_DOCTYPE, {
  day_count(frm, cdt, cdn) {
    if (isAutoFilling) return;
    recalculate_row_amount(frm, cdt, cdn);
  },
  rate_per_day(frm, cdt, cdn) {
    if (isAutoFilling) return;
    recalculate_row_amount(frm, cdt, cdn);
  },
});

function recalculate_row_amount(frm, cdt, cdn) {
  const row = locals[cdt][cdn];

  const days = flt(row.day_count || 0);
  const rate = flt(row.rate_per_day || 0);

  let amount = days * rate;

  if ((row.component || "").trim() === "Worked Day") {
    amount = Math.min(days, 30) * rate;
  }

  frappe.model.set_value(cdt, cdn, "amount", amount);

  update_total_from_table(frm);
  frm.refresh_field("payables");
}

function update_total_from_table(frm) {
  let total = 0;
  (frm.doc.payables || []).forEach((row) => {
    total += flt(row.amount || 0);
  });
  frm.set_value("total_payable_amount", total);
}