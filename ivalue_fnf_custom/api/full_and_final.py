import frappe
from datetime import date, timedelta
from frappe.utils import getdate, nowdate, flt
from dateutil.relativedelta import relativedelta


# -----------------------------
# Helpers - Salary Structure Assignment
# -----------------------------
def _get_active_salary_structure_assignment(employee: str, ref_date: date):
    """
    ✅ شرح عربي:
    يجيب آخر Salary Structure Assignment للموظف (Submitted) وتاريخه <= ref_date
    """
    ssa_name = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": employee, "docstatus": 1, "from_date": ("<=", ref_date)},
        "name",
        order_by="from_date desc",
    )
    return frappe.get_doc("Salary Structure Assignment", ssa_name) if ssa_name else None


def _build_salary_formula_context(ssa):
    """
    ✅ شرح عربي:
    نبني context للفورمولات عشان نحاول نحسب formulas داخل Salary Structure:
    - base
    - custom_total (لو موجود)
    - أي حقول رقمية داخل SSA
    """
    ctx = {}

    try:
        ssa_dict = ssa.as_dict()
    except Exception:
        ssa_dict = {}

    ctx["base"] = flt(getattr(ssa, "base", 0) or 0)
    ctx["custom_total"] = flt(getattr(ssa, "custom_total", 0) or 0)

    for k, v in (ssa_dict or {}).items():
        try:
            if isinstance(v, (int, float)):
                ctx[k] = flt(v)
        except Exception:
            pass

    return ctx


def _safe_eval_salary_formula(formula: str, ctx: dict):
    """
    ✅ شرح عربي:
    نحسب formula بشكل آمن.
    إذا فشل الحساب لأي سبب → نرجع 0 بدون كسر النظام.
    """
    if not formula:
        return 0
    try:
        return flt(frappe.safe_eval(formula, None, ctx))
    except Exception:
        return 0


def _sum_salary_structure_earnings_with_formulas(salary_structure_name: str, ssa):
    """
    ✅ شرح عربي:
    يجمع الراتب الشهري من Salary Structure -> earnings:
    - amount إن كان موجود
    - formula إن كان موجود (نحاول نحسبها)
    """
    try:
        ss = frappe.get_doc("Salary Structure", salary_structure_name)
    except Exception:
        return 0

    ctx = _build_salary_formula_context(ssa)

    total = 0
    if hasattr(ss, "earnings") and ss.earnings:
        for e in ss.earnings:
            amount = flt(getattr(e, "amount", 0) or 0)
            formula = (getattr(e, "formula", "") or "").strip()

            if amount:
                total += amount
                continue

            if formula:
                total += flt(_safe_eval_salary_formula(formula, ctx))

    return flt(total)


def _get_monthly_salary_from_ssa(ssa) -> float:
    """
    ✅ شرح عربي:
    حسب طلبك النهائي:
    الراتب الشهري يُحسب من earnings داخل Salary Structure.
    لو ما قدرنا نجيب Salary Structure لأي سبب، نرجع fallback على custom_total/base
    """
    if not ssa:
        return 0

    salary_structure = getattr(ssa, "salary_structure", None)
    if salary_structure:
        total = _sum_salary_structure_earnings_with_formulas(salary_structure, ssa)
        if total:
            return total

    # fallback
    custom_total = flt(getattr(ssa, "custom_total", 0) or 0)
    base = flt(getattr(ssa, "base", 0) or 0)
    return custom_total if custom_total else base


# -----------------------------
# Helpers - Dates
# -----------------------------
def _days_inclusive(start_date: date, end_date: date) -> int:
    """
    ✅ شرح عربي:
    حساب أيام inclusive (يشمل البداية والنهاية)
    """
    if not start_date or not end_date:
        return 0
    if end_date < start_date:
        return 0
    return (end_date - start_date).days + 1


def _is_end_of_month(d: date) -> bool:
    """
    ✅ شرح عربي:
    هل التاريخ آخر يوم في الشهر؟
    """
    first_next = date(
        d.year + (1 if d.month == 12 else 0),
        (1 if d.month == 12 else d.month + 1),
        1,
    )
    last_day = first_next - timedelta(days=1)
    return d == last_day


def _get_calc_date(employee: str, transaction_date: str = None, as_of_date: str = None):
    """
    ✅ شرح عربي:
    نستخدم Relieving Date إن وجد، وإلا transaction_date، وإلا as_of_date، وإلا اليوم
    """
    relieving_date = frappe.db.get_value("Employee", employee, "relieving_date")
    if relieving_date:
        return getdate(relieving_date)
    if transaction_date:
        return getdate(transaction_date)
    if as_of_date:
        return getdate(as_of_date)
    return getdate(nowdate())


# -----------------------------
# Helpers - Salary Slip
# -----------------------------
def _get_last_salary_slip(employee: str):
    """
    ✅ شرح عربي:
    نجيب آخر Salary Slip (Submitted) للموظف.
    """
    slip_name = frappe.db.get_value(
        "Salary Slip",
        {"employee": employee, "docstatus": 1},
        "name",
        order_by="end_date desc, posting_date desc",
    )
    return frappe.get_doc("Salary Slip", slip_name) if slip_name else None


# -----------------------------
# Whitelisted - Validate
# -----------------------------
@frappe.whitelist()
def validate_employee_relieve_and_status(employee: str, transaction_date: str = None):
    """
    ✅ شرح عربي:
    يتحقق:
    - Relieving Date موجود
    - Status مش Active (حسب كودك الحالي)
    """
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    emp = frappe.db.get_value("Employee", employee, ["status", "relieving_date"], as_dict=True)
    if not emp:
        return {"ok": False, "msg": "Employee not found"}

    if not emp.relieving_date:
        return {"ok": False, "msg": "Relieving Date is missing"}

    status = (emp.status or "").strip().lower()
    if status == "active":
        return {"ok": False, "msg": "Employee is still Active. Please set Status to Left and try again."}

    return {"ok": True}


# -----------------------------
# Payroll Context
# -----------------------------
def _get_payroll_context_internal(employee: str, d: date, period_from: str = None, period_to: str = None):
    """
    ✅ شرح عربي:
    يحسب:
    - monthly_salary من SSA (لكن حسب earnings داخل Salary Structure)
    - rate_per_day = monthly_salary / 30
    - worked_days = عدد الأيام بين period_from و period_to (inclusive)
    - calculated_amount = worked_days * rate_per_day
      * إذا period_to آخر الشهر: calculated_amount = monthly_salary (حسب منطقك السابق)
    """
    ssa = _get_active_salary_structure_assignment(employee, d)
    if not ssa:
        return {
            "ok": False,
            "msg": "No Salary Structure Assignment found (Submitted).",
            "source": "none",
            "monthly_salary": 0,
            "divisor": 30,
            "rate_per_day": 0,
            "worked_days": 0,
            "calculated_amount": 0,
            "salary_structure_assignment": None,
            "period_from": None,
            "period_to": None,
        }

    monthly_salary = flt(_get_monthly_salary_from_ssa(ssa))
    divisor = 30.0
    rate_per_day = flt(monthly_salary / divisor) if divisor else 0.0

    pf = getdate(period_from) if period_from else date(d.year, d.month, 1)
    pt = getdate(period_to) if period_to else d

    worked_days = flt(_days_inclusive(pf, pt))

    if _is_end_of_month(pt):
        calculated_amount = flt(monthly_salary)
        worked_days_display = 30.0
    else:
        calculated_amount = flt(worked_days * rate_per_day)
        worked_days_display = worked_days

    return {
        "ok": True,
        "msg": "",
        "source": "salary_structure_assignment",
        "monthly_salary": monthly_salary,
        "divisor": divisor,
        "rate_per_day": rate_per_day,
        "period_from": str(pf),
        "period_to": str(pt),
        "worked_days": worked_days_display,
        "calculated_amount": calculated_amount,
        "salary_structure_assignment": ssa.name,
    }


@frappe.whitelist()
def get_payroll_context(employee: str, transaction_date: str = None, period_from: str = None, period_to: str = None):
    if not employee:
        return {
            "ok": False,
            "msg": "Employee is required",
            "source": "none",
            "monthly_salary": 0,
            "worked_days": 0,
            "divisor": 30,
            "rate_per_day": 0,
            "calculated_amount": 0,
        }

    d = _get_calc_date(employee, transaction_date=transaction_date)
    return _get_payroll_context_internal(employee, d, period_from=period_from, period_to=period_to)


# -----------------------------
# Service Duration
# -----------------------------
def _get_employee_service_internal(employee: str, as_of_date: date):
    doj = frappe.db.get_value("Employee", employee, "date_of_joining")
    if not doj:
        return {"ok": False, "msg": "Employee has no Date of Joining"}

    doj = getdate(doj)
    if as_of_date < doj:
        return {
            "ok": True,
            "years": 0,
            "months": 0,
            "days": 0,
            "custom_total_of_years": 0.0,
            "date_of_joining": str(doj),
            "as_of_date": str(as_of_date),
        }

    rd = relativedelta(as_of_date, doj)
    total_years = rd.years + (rd.months / 12) + (rd.days / 365)

    return {
        "ok": True,
        "years": rd.years,
        "months": rd.months,
        "days": rd.days,
        "custom_total_of_years": total_years,
        "date_of_joining": str(doj),
        "as_of_date": str(as_of_date),
    }


@frappe.whitelist()
def get_employee_info(employee: str, as_of_date: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    end = getdate(as_of_date) if as_of_date else getdate(nowdate())
    return _get_employee_service_internal(employee, end)


# -----------------------------
# Leave Encashment (Allow Encashment only)
# -----------------------------
def _get_leave_encashment_internal(employee: str, d: date):
    """
    ✅ شرح عربي:
    - يجيب leave details من HRMS
    - ياخذ فقط Leave Types اللي allow_encashment = 1
    - يجمع remaining_leaves كـ days
    - amount = days * rate_per_day
    """
    try:
        from hrms.hr.doctype.leave_application.leave_application import get_leave_details
    except Exception:
        return {"ok": False, "msg": "Unable to import get_leave_details from HRMS."}

    details = get_leave_details(employee, d) or {}
    leave_allocation = details.get("leave_allocation") or {}

    if not leave_allocation and isinstance(details.get("message"), dict):
        leave_allocation = details["message"].get("leave_allocation") or {}

    if not isinstance(leave_allocation, dict) or not leave_allocation:
        return {"ok": False, "msg": f"No leave allocation data returned as of {d}."}

    ctx = _get_payroll_context_internal(employee, d)
    rate = flt((ctx or {}).get("rate_per_day") or 0)
    if rate <= 0:
        return {"ok": False, "msg": "Rate per day is 0. Check Salary Structure Assignment / Salary Structure earnings."}

    encashable_types = set(
        frappe.get_all("Leave Type", filters={"allow_encashment": 1}, pluck="name")
    )

    total_days = 0.0
    rows = []

    for lt, data in leave_allocation.items():
        if lt not in encashable_types:
            continue
        remaining = flt((data or {}).get("remaining_leaves") or 0)
        if remaining > 0:
            total_days += remaining
            rows.append({"leave_type": lt, "days": remaining})

    amount = flt(total_days * rate)

    return {
        "ok": True,
        "as_of_date": str(d),
        "rate": round(rate, 6),
        "days": round(total_days, 2),
        "amount": round(amount, 2),
        "breakdown": rows,
    }


@frappe.whitelist()
def get_leave_encashment(employee: str, as_of_date: str = None, leave_type: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}
    d = _get_calc_date(employee, as_of_date=as_of_date)
    return _get_leave_encashment_internal(employee, d)


# -----------------------------
# Main Payload
# -----------------------------
@frappe.whitelist()
def get_full_and_final_payload(employee: str, transaction_date: str = None):
    """
    ✅ شرح عربي:
    يرجع Payload كامل للـ JS:
    - service
    - payroll (worked days + rate)
    - leave encashment
    - payables table (بأعمدة day_count/amount/reference_*)
    - totals
    """
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    d = _get_calc_date(employee, transaction_date=transaction_date)

    # validate
    v = validate_employee_relieve_and_status(employee=employee, transaction_date=str(d))
    if not v.get("ok"):
        return {"ok": False, "msg": v.get("msg")}

    service = _get_employee_service_internal(employee, d)
    if not service.get("ok"):
        return {"ok": False, "msg": service.get("msg")}

    # ======================================================
    # ✅ Worked Days period:
    # - من بعد آخر Salary Slip (end_date + 1)
    # - وإذا ما في Salary Slip: من Date of Joining
    # ======================================================
    last_slip = _get_last_salary_slip(employee)

    doj = frappe.db.get_value("Employee", employee, "date_of_joining")
    doj = getdate(doj) if doj else None

    if last_slip and last_slip.end_date:
        period_from = getdate(last_slip.end_date) + timedelta(days=1)
        ref_doc_type_worked = "Salary Slip"
        ref_doc_worked = last_slip.name
    else:
        period_from = doj if doj else d
        ref_doc_type_worked = "Employee"
        ref_doc_worked = employee

    period_to = d

    payroll = _get_payroll_context_internal(
        employee,
        d,
        period_from=str(period_from),
        period_to=str(period_to),
    )

    if not payroll.get("ok"):
        return {"ok": False, "msg": payroll.get("msg")}

    leave = _get_leave_encashment_internal(employee, d)
    if not leave.get("ok"):
        # إذا ما قدر يجيب الإجازات، نخليها صفر بدل ما نوقف الحساب
        leave = {
            "ok": True,
            "as_of_date": str(d),
            "rate": payroll.get("rate_per_day", 0),
            "days": 0,
            "amount": 0,
            "breakdown": [],
        }

    payables = []

    # ======================================
    # Row 1: Worked Day
    # ======================================
    worked_days = flt(payroll.get("worked_days") or 0)
    worked_amount = flt(payroll.get("calculated_amount") or 0)

    payables.append({
        "component": "Worked Day",
        "day_count": worked_days,
        "amount": worked_amount,
        "reference_document_type": ref_doc_type_worked,
        "reference_document": ref_doc_worked,

        # نتركها كمان لو عندك حقول إضافية
        "worked_days": worked_days,
        "rate_per_day": flt(payroll.get("rate_per_day") or 0),
        "auto_amount": worked_amount,
    })

    # ======================================
    # Row 2: Leave Encashment
    # ======================================
    leave_days = flt(leave.get("days") or 0)
    leave_amount = flt(leave.get("amount") or 0)

    breakdown = leave.get("breakdown") or []
    leave_types = [x.get("leave_type") for x in breakdown if x.get("leave_type")]

    if len(leave_types) == 1:
        ref_leave_doc = leave_types[0]
    elif len(leave_types) > 1:
        ref_leave_doc = "Multiple"
    else:
        ref_leave_doc = ""

    payables.append({
        "component": "Leave Encashment",
        "day_count": leave_days,
        "amount": leave_amount,
        "reference_document_type": "Leave Type",
        "reference_document": ref_leave_doc,

        "worked_days": leave_days,
        "rate_per_day": flt(leave.get("rate") or 0),
        "auto_amount": leave_amount,
    })

    # totals
    total_payable = 0.0
    for r in payables:
        total_payable += flt(r.get("amount") or r.get("auto_amount") or 0)

    return {
        "ok": True,
        "as_of_date": str(d),
        "service": {
            "years": service.get("years"),
            "months": service.get("months"),
            "days": service.get("days"),
            "custom_total_of_years": service.get("custom_total_of_years"),
        },
        "payroll": payroll,
        "leave_encashment": leave,
        "payables": payables,
        "totals": {
            "leave_encashment_amount": flt(leave.get("amount") or 0),
            "total_payable": flt(total_payable),
        },
        "note": "",
    }