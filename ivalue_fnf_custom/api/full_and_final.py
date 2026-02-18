import calendar
import frappe
from datetime import date, timedelta
from frappe.utils import getdate, nowdate, flt
from dateutil.relativedelta import relativedelta


# =========================================================
# Salary / Payroll Helpers
# =========================================================
def get_active_salary_structure_assignment(employee_id: str, reference_date: date):
    assignment_name = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": employee_id, "docstatus": 1, "from_date": ("<=", reference_date)},
        "name",
        order_by="from_date desc",
    )
    return frappe.get_doc("Salary Structure Assignment", assignment_name) if assignment_name else None


def build_salary_formula_context(assignment_doc):
    context = {}

    try:
        assignment_dict = assignment_doc.as_dict()
    except Exception:
        assignment_dict = {}

    context["base"] = flt(getattr(assignment_doc, "base", 0) or 0)
    context["custom_total"] = flt(getattr(assignment_doc, "custom_total", 0) or 0)

    for field_name, value in (assignment_dict or {}).items():
        if isinstance(value, (int, float)):
            context[field_name] = flt(value)

    return context


def safe_eval_salary_formula(formula: str, context: dict) -> float:
    if not formula:
        return 0.0
    try:
        return flt(frappe.safe_eval(formula, None, context))
    except Exception:
        return 0.0


def sum_salary_structure_earnings(salary_structure_name: str, assignment_doc) -> float:
    try:
        salary_structure = frappe.get_doc("Salary Structure", salary_structure_name)
    except Exception:
        return 0.0

    context = build_salary_formula_context(assignment_doc)

    total_earnings = 0.0
    if getattr(salary_structure, "earnings", None):
        for earning_row in salary_structure.earnings:
            row_amount = flt(getattr(earning_row, "amount", 0) or 0)
            row_formula = (getattr(earning_row, "formula", "") or "").strip()

            if row_amount:
                total_earnings += row_amount
            elif row_formula:
                total_earnings += safe_eval_salary_formula(row_formula, context)

    return flt(total_earnings)


def get_monthly_salary(assignment_doc) -> float:
    """
    Monthly salary = sum of Salary Structure earnings (amount/formula)
    fallback: custom_total/base
    """
    if not assignment_doc:
        return 0.0

    salary_structure_name = getattr(assignment_doc, "salary_structure", None)
    if salary_structure_name:
        monthly_from_structure = sum_salary_structure_earnings(salary_structure_name, assignment_doc)
        if monthly_from_structure:
            return monthly_from_structure

    fallback_custom_total = flt(getattr(assignment_doc, "custom_total", 0) or 0)
    fallback_base = flt(getattr(assignment_doc, "base", 0) or 0)
    return fallback_custom_total if fallback_custom_total else fallback_base


# =========================================================
# Date Helpers
# =========================================================
def days_inclusive(start_date: date, end_date: date) -> int:
    if not start_date or not end_date or end_date < start_date:
        return 0
    return (end_date - start_date).days + 1


def first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def is_end_of_month(d: date) -> bool:
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day == last_day


def get_calculation_date(employee_id: str, transaction_date: str = None, as_of_date: str = None) -> date:
    """
    Priority:
    1) transaction_date (from form)
    2) employee.relieving_date
    3) as_of_date
    4) today
    """
    if transaction_date:
        return getdate(transaction_date)

    relieving_date = frappe.db.get_value("Employee", employee_id, "relieving_date")
    if relieving_date:
        return getdate(relieving_date)

    if as_of_date:
        return getdate(as_of_date)

    return getdate(nowdate())


# =========================================================
# Salary Slip Helpers
# =========================================================
def get_last_submitted_salary_slip(employee_id: str):
    slip_name = frappe.db.get_value(
        "Salary Slip",
        {"employee": employee_id, "docstatus": 1},
        "name",
        order_by="end_date desc, posting_date desc",
    )
    return frappe.get_doc("Salary Slip", slip_name) if slip_name else None


# =========================================================
# Validations
# =========================================================
@frappe.whitelist()
def validate_employee_relieve_and_status(employee: str, transaction_date: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    employee_data = frappe.db.get_value("Employee", employee, ["status", "relieving_date"], as_dict=True)
    if not employee_data:
        return {"ok": False, "msg": "Employee not found"}

    if not employee_data.relieving_date:
        return {"ok": False, "msg": "Relieving Date is missing"}

    employee_status = (employee_data.status or "").strip().lower()
    if employee_status == "active":
        return {"ok": False, "msg": "Employee is still Active. Please set Status to Left and try again."}

    return {"ok": True}


# =========================================================
# Payroll Context (Worked Days + Amount)
# =========================================================
def get_payroll_context_internal(employee_id: str, calc_date: date, period_start: date, period_end: date):
    assignment_doc = get_active_salary_structure_assignment(employee_id, calc_date)
    if not assignment_doc:
        return {
            "ok": False,
            "msg": "No Salary Structure Assignment found (Submitted).",
            "monthly_salary": 0,
            "rate_per_day": 0,
            "worked_days": 0,
            "calculated_amount": 0,
            "salary_structure_assignment": None,
            "period_from": str(period_start),
            "period_to": str(period_end),
        }

    monthly_salary = flt(get_monthly_salary(assignment_doc))
    divisor_days = 30.0
    daily_rate = flt(monthly_salary / divisor_days) if divisor_days else 0.0

    if period_end < period_start:
        return {
            "ok": True,
            "msg": "",
            "monthly_salary": monthly_salary,
            "rate_per_day": daily_rate,
            "worked_days": 0,
            "calculated_amount": 0,
            "salary_structure_assignment": assignment_doc.name,
            "period_from": str(period_start),
            "period_to": str(period_end),
        }

    worked_days = flt(days_inclusive(period_start, period_end))

    # ملاحظة: أبقيت منطقك (إذا آخر يوم بالشهر => راتب شهر كامل)
    # إذا بدك، بنعدله لاحقاً ليصير proportional دائماً.
    if is_end_of_month(period_end):
        calculated_amount = flt(monthly_salary)
        worked_days_display = 30.0
    else:
        calculated_amount = flt(worked_days * daily_rate)
        worked_days_display = worked_days

    return {
        "ok": True,
        "msg": "",
        "monthly_salary": monthly_salary,
        "rate_per_day": daily_rate,
        "worked_days": worked_days_display,
        "calculated_amount": calculated_amount,
        "salary_structure_assignment": assignment_doc.name,
        "period_from": str(period_start),
        "period_to": str(period_end),
    }


@frappe.whitelist()
def get_payroll_context(employee: str, transaction_date: str = None, period_from: str = None, period_to: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    calc_date = get_calculation_date(employee, transaction_date=transaction_date)
    period_start = getdate(period_from) if period_from else first_day_of_month(calc_date)
    period_end = getdate(period_to) if period_to else calc_date

    return get_payroll_context_internal(employee, calc_date, period_start, period_end)


# =========================================================
# Service Duration
# =========================================================
def get_employee_service(employee_id: str, as_of_date: date):
    date_of_joining = frappe.db.get_value("Employee", employee_id, "date_of_joining")
    if not date_of_joining:
        return {"ok": False, "msg": "Employee has no Date of Joining"}

    date_of_joining = getdate(date_of_joining)

    if as_of_date < date_of_joining:
        return {
            "ok": True,
            "years": 0,
            "months": 0,
            "days": 0,
            "total_years_decimal": 0.0,
            "date_of_joining": str(date_of_joining),
            "as_of_date": str(as_of_date),
        }

    delta = relativedelta(as_of_date, date_of_joining)
    total_years_decimal = delta.years + (delta.months / 12) + (delta.days / 365)

    return {
        "ok": True,
        "years": delta.years,
        "months": delta.months,
        "days": delta.days,
        "total_years_decimal": flt(total_years_decimal),
        "date_of_joining": str(date_of_joining),
        "as_of_date": str(as_of_date),
    }


@frappe.whitelist()
def get_employee_info(employee: str, as_of_date: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}
    end_date = getdate(as_of_date) if as_of_date else getdate(nowdate())
    return get_employee_service(employee, end_date)


# =========================================================
# Leave Encashment (Daily Accrual, 28/30/31, NO rounding)
# =========================================================
def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def last_day_of_month(d: date) -> date:
    return date(d.year, d.month, days_in_month(d))


def get_total_allocated_days_from_leave_data(leave_data: dict) -> float:
    """
    Annual entitlement (e.g., 21 days) from leave_data.
    Keys differ by HRMS version, so we use fallbacks.
    """
    possible_keys = [
        "total_leaves",
        "total_allocated_leaves",
        "total_leaves_allocated",
        "new_leaves_allocated",
        "allocated_leaves",
    ]
    for key in possible_keys:
        value = (leave_data or {}).get(key)
        if value is not None:
            return flt(value)
    return 0.0


def get_taken_days_from_leave_data(leave_data: dict) -> float:
    possible_keys = ["leaves_taken", "used_leaves", "leaves_used"]
    for key in possible_keys:
        value = (leave_data or {}).get(key)
        if value is not None:
            return flt(value)
    return 0.0


def calculate_daily_accrued_leave_days(start_date: date, end_date: date, annual_entitlement_days: float) -> float:
    """
    Daily accrual:
    - full month: annual/12
    - partial month: (annual/12) / days_in_month * worked_days_in_that_month
    No rounding.
    """
    if not start_date or not end_date or end_date < start_date:
        return 0.0

    monthly_entitlement = flt(annual_entitlement_days) / 12.0
    accrued_total = 0.0

    current_date = start_date
    while current_date <= end_date:
        month_start = first_day_of_month(current_date)
        month_end = last_day_of_month(current_date)

        segment_start = current_date
        segment_end = min(end_date, month_end)

        worked_days_in_segment = (segment_end - segment_start).days + 1
        month_days = days_in_month(current_date)

        if segment_start == month_start and segment_end == month_end:
            accrued_total += monthly_entitlement
        else:
            accrued_total += (monthly_entitlement / month_days) * worked_days_in_segment

        current_date = segment_end + timedelta(days=1)

    return flt(accrued_total)


def get_annual_leave_type_names():
    """
    Prefer custom flag if exists:
      Leave Type.custom_is_annual_leave = 1
    fallback:
      allow_encashment = 1
    """
    if frappe.db.has_column("Leave Type", "custom_is_annual_leave"):
        flagged = frappe.get_all("Leave Type", filters={"custom_is_annual_leave": 1}, pluck="name")
        if flagged:
            return set(flagged)

    # fallback as you had
    return set(frappe.get_all("Leave Type", filters={"allow_encashment": 1}, pluck="name"))


def get_leave_encashment_daily(employee_id: str, calc_date: date):
    """
    Returns:
      days = accrued - taken
      amount = days * payroll_rate_per_day
    """
    try:
        from hrms.hr.doctype.leave_application.leave_application import get_leave_details
    except Exception:
        return {"ok": False, "msg": "Unable to import get_leave_details from HRMS."}

    leave_details = get_leave_details(employee_id, calc_date) or {}
    leave_allocation_map = leave_details.get("leave_allocation") or {}

    if not leave_allocation_map and isinstance(leave_details.get("message"), dict):
        leave_allocation_map = leave_details["message"].get("leave_allocation") or {}

    if not isinstance(leave_allocation_map, dict) or not leave_allocation_map:
        return {"ok": False, "msg": f"No leave allocation data returned as of {calc_date}."}

    # payroll daily rate
    payroll_context = get_payroll_context_internal(
        employee_id,
        calc_date,
        period_start=first_day_of_month(calc_date),
        period_end=calc_date,
    )
    daily_rate = flt((payroll_context or {}).get("rate_per_day") or 0)
    if daily_rate <= 0:
        return {"ok": False, "msg": "Rate per day is 0. Check Salary Structure Assignment / Salary Structure earnings."}

    date_of_joining = frappe.db.get_value("Employee", employee_id, "date_of_joining")
    if not date_of_joining:
        return {"ok": False, "msg": "Employee has no Date of Joining"}
    date_of_joining = getdate(date_of_joining)

    annual_leave_types = get_annual_leave_type_names()

    total_earned_remaining_days = 0.0
    breakdown_rows = []

    for leave_type_name, leave_type_data in leave_allocation_map.items():
        if leave_type_name not in annual_leave_types:
            continue

        annual_entitlement_days = get_total_allocated_days_from_leave_data(leave_type_data)

        # fallback (last resort) if HRMS didn't provide entitlement clearly
        if annual_entitlement_days <= 0:
            annual_entitlement_days = flt((leave_type_data or {}).get("remaining_leaves") or 0)

        if annual_entitlement_days <= 0:
            continue

        accrued_days = calculate_daily_accrued_leave_days(date_of_joining, calc_date, annual_entitlement_days)
        taken_days = get_taken_days_from_leave_data(leave_type_data)

        earned_remaining_days = flt(accrued_days - taken_days)
        if earned_remaining_days < 0:
            earned_remaining_days = 0.0

        if earned_remaining_days > 0:
            total_earned_remaining_days += earned_remaining_days
            breakdown_rows.append({
                "leave_type": leave_type_name,
                "days": earned_remaining_days,
                "annual_entitlement": annual_entitlement_days,
                "accrued": accrued_days,
                "taken": taken_days,
            })

    total_amount = flt(total_earned_remaining_days * daily_rate)

    return {
        "ok": True,
        "as_of_date": str(calc_date),
        "rate": daily_rate,
        "days": total_earned_remaining_days,
        "amount": total_amount,
        "breakdown": breakdown_rows,
    }


# =========================================================
# Main Payload (API for JS)
# =========================================================
@frappe.whitelist()
def get_full_and_final_payload(employee: str, transaction_date: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    calc_date = get_calculation_date(employee, transaction_date=transaction_date)

    validation_result = validate_employee_relieve_and_status(employee=employee, transaction_date=str(calc_date))
    if not validation_result.get("ok"):
        return {"ok": False, "msg": validation_result.get("msg")}

    service_result = get_employee_service(employee, calc_date)
    if not service_result.get("ok"):
        return {"ok": False, "msg": service_result.get("msg")}

    # ==========================
    # Worked Days period start (current month only)
    # ==========================
    last_salary_slip = get_last_submitted_salary_slip(employee)

    date_of_joining = frappe.db.get_value("Employee", employee, "date_of_joining")
    date_of_joining = getdate(date_of_joining) if date_of_joining else None

    month_start = first_day_of_month(calc_date)

    period_start_candidates = [month_start]

    if date_of_joining and month_start < date_of_joining <= calc_date:
        period_start_candidates.append(date_of_joining)

    reference_document_type = "Employee"
    reference_document_name = employee

    if last_salary_slip and last_salary_slip.end_date:
        start_after_last_slip = getdate(last_salary_slip.end_date) + timedelta(days=1)
        if month_start < start_after_last_slip <= calc_date:
            period_start_candidates.append(start_after_last_slip)
            reference_document_type = "Salary Slip"
            reference_document_name = last_salary_slip.name

    worked_period_start = max(period_start_candidates)
    worked_period_end = calc_date

    payroll_context = get_payroll_context_internal(
        employee,
        calc_date,
        period_start=worked_period_start,
        period_end=worked_period_end,
    )
    if not payroll_context.get("ok"):
        return {"ok": False, "msg": payroll_context.get("msg")}

    # Leave Encashment daily
    leave_result = get_leave_encashment_daily(employee, calc_date)
    leave_note = ""
    if not leave_result.get("ok"):
        leave_note = leave_result.get("msg") or "Leave encashment failed"
        leave_result = {
            "ok": True,
            "as_of_date": str(calc_date),
            "rate": flt(payroll_context.get("rate_per_day") or 0),
            "days": 0.0,
            "amount": 0.0,
            "breakdown": [],
        }

    payables_rows = []

    # Row 1: Worked Day
    worked_days = flt(payroll_context.get("worked_days") or 0)
    worked_amount = flt(payroll_context.get("calculated_amount") or 0)

    payables_rows.append({
        "component": "Worked Day",
        "day_count": worked_days,
        "amount": worked_amount,
        "reference_document_type": reference_document_type,
        "reference_document": reference_document_name,
        "worked_days": worked_days,
        "rate_per_day": flt(payroll_context.get("rate_per_day") or 0),
        "auto_amount": worked_amount,
    })

    # Row 2: Leave Encashment
    leave_days = flt(leave_result.get("days") or 0)
    leave_amount = flt(leave_result.get("amount") or 0)

    breakdown = leave_result.get("breakdown") or []
    leave_types = [x.get("leave_type") for x in breakdown if x.get("leave_type")]

    if len(leave_types) == 1:
        leave_reference = leave_types[0]
    elif len(leave_types) > 1:
        leave_reference = "Multiple"
    else:
        leave_reference = ""

    payables_rows.append({
        "component": "Leave Encashment",
        "day_count": leave_days,
        "amount": leave_amount,
        "reference_document_type": "Leave Type",
        "reference_document": leave_reference,
        "worked_days": leave_days,
        "rate_per_day": flt(leave_result.get("rate") or 0),
        "auto_amount": leave_amount,
    })

    total_payable_amount = 0.0
    for row in payables_rows:
        total_payable_amount += flt(row.get("amount") or row.get("auto_amount") or 0)

    return {
        "ok": True,
        "as_of_date": str(calc_date),
        "service": {
            "years": service_result.get("years"),
            "months": service_result.get("months"),
            "days": service_result.get("days"),
            "custom_total_of_years": service_result.get("total_years_decimal"),
        },
        "payroll": payroll_context,
        "leave_encashment": leave_result,
        "payables": payables_rows,
        "totals": {
            "leave_encashment_amount": flt(leave_result.get("amount") or 0),
            "total_payable": flt(total_payable_amount),
        },
        "note": leave_note,
    }