const CHILD_DTYPE_NAME = "Full and Final Outstanding Statement";

frappe.ui.form.on("Full and Final Statement", {
  onload(frm) {
    toggle_sections(frm, !!frm.doc.employee, false);

    // ✅ تعبئة Transaction Date بتاريخ اليوم إذا فاضي
    if (!frm.doc.transaction_date) {
      frm.set_value("transaction_date", frappe.datetime.get_today());
    }

    // إذا الموظف موجود عند فتح الفورم، احسب مباشرة
    if (frm.doc.employee) {
      recalc_full_and_final(frm);
    }
  },

  async employee(frm) {
    toggle_sections(frm, !!frm.doc.employee, true);

    // إذا تم حذف الموظف → تفريغ كل شيء
    if (!frm.doc.employee) {
      clear_all(frm);
      return;
    }

    await recalc_full_and_final(frm);
  },

  async transaction_date(frm) {
    if (!frm.doc.employee) return;
    await recalc_full_and_final(frm);
  },

  async custom_recalculate(frm) {
    if (!frm.doc.employee) return;
    await recalc_full_and_final(frm);
  },
});

async function recalc_full_and_final(frm) {
  // ✅ Call واحد فقط
  const r = await frm.call({
    method: "ivalue_fnf_custom.api.full_and_final.get_full_and_final_payload",
    args: {
      employee: frm.doc.employee,
      transaction_date: frm.doc.transaction_date,
    },
  });

  const data = r.message;

  if (!data || !data.ok) {
    frappe.msgprint(data?.msg || "Unable to calculate Full & Final.");
    return;
  }

  // ✅ تعبئة حقول الخدمة + totals
  frm.set_value({
    custom_service_years: data.service?.years || 0,
    custom_service_month: data.service?.months || 0,
    custom_service_days: data.service?.days || 0,
    custom_total_of_years: data.service?.custom_total_of_years || 0,

    custom_leave_encashment_amount: flt(data.totals?.leave_encashment_amount || 0),

    // total_payable_amount يتعبى من totals
    total_payable_amount: flt(data.totals?.total_payable || 0),
  });

  // ✅ تعبئة جدول Payables بالأعمدة اللي عندك (Amount/Day count/Reference*)
  frm.clear_table("payables");

  (data.payables || []).forEach((row) => {
    const child = frm.add_child("payables");

    // component
    child.component = row.component || "";

    // ✅ مهم: عبّي الأعمدة مباشرة (بدون if على قيمة الحقل)
    child.day_count = flt(row.day_count || 0);
    child.amount = flt(row.amount || 0);

    child.reference_document_type = row.reference_document_type || "";
    child.reference_document = row.reference_document || "";

    // ✅ لو في حقول إضافية موجودة عندك (مش ضروري لكنها ما بتضر)
    if (child.worked_days !== undefined) {
      child.worked_days = flt(row.worked_days || 0);
    }
    if (child.rate_per_day !== undefined) {
      child.rate_per_day = flt(row.rate_per_day || 0);
    }
    if (child.auto_amount !== undefined) {
      child.auto_amount = flt(row.auto_amount || row.amount || 0);
    }
  });

  frm.refresh_field("payables");
}

/* -----------------------------------------------------------
   تفريغ كل البيانات عند حذف الموظف
----------------------------------------------------------- */
function clear_all(frm) {
  frm.clear_table("payables");
  frm.refresh_field("payables");

  frm.set_value({
    custom_service_years: 0,
    custom_service_month: 0,
    custom_service_days: 0,
    custom_total_of_years: 0,
    custom_leave_encashment_amount: 0,
    total_payable_amount: 0,
  });
}

/* -----------------------------------------------------------
   التحكم في ظهور الأقسام
----------------------------------------------------------- */
function toggle_sections(frm, show, animate = true) {
  const sections = [
    "custom_service_detalies",
    "section_break_8",
    "section_break_10",
    "section_break_15",
    "totals_section",
    "employee_details_section",
    "custom_final_settlement_section",
  ];

  sections.forEach((sec) => {
    const field = frm.get_field(sec);
    if (!field) return;

    const wrapper = $(field.wrapper);
    if (show) {
      animate ? wrapper.stop().slideDown(400) : wrapper.show();
    } else {
      animate ? wrapper.stop().slideUp(400) : wrapper.hide();
    }
  });
}

/* -----------------------------------------------------------
   لو المستخدم عدّل day_count أو rate_per_day يدويًا
   نحسب amount ونحدّث Total Payable
----------------------------------------------------------- */
frappe.ui.form.on(CHILD_DTYPE_NAME, {
  day_count(frm, cdt, cdn) {
    recalc_row_amount(frm, cdt, cdn);
  },
  rate_per_day(frm, cdt, cdn) {
    recalc_row_amount(frm, cdt, cdn);
  },
});

function recalc_row_amount(frm, cdt, cdn) {
  const row = locals[cdt][cdn];

  const days = flt(row.day_count || 0);
  const rate = flt(row.rate_per_day || 0);

  let amount = days * rate;

  // Worked Day cap 30
  if ((row.component || "").trim() === "Worked Day") {
    amount = Math.min(days, 30) * rate;
  }

  // ✅ اكتب الناتج على amount
  frappe.model.set_value(cdt, cdn, "amount", amount);

  // ✅ حدّث total_payable_amount من جدول payables
  let total = 0;
  (frm.doc.payables || []).forEach((r) => {
    total += flt(r.amount || 0);
  });
  frm.set_value("total_payable_amount", total);

  frm.refresh_field("payables");
}