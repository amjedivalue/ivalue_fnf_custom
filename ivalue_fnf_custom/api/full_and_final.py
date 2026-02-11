import frappe
from datetime import date, timedelta
from frappe.utils import getdate, nowdate, flt
from dateutil.relativedelta import relativedelta


# -----------------------------
# Helpers
# -----------------------------
def _get_active_salary_structure_assignment(employee: str, ref_date: date):

    ssa_name = frappe.db.get_value(
        "Salary Structure Assignment",
        {"employee": employee, "docstatus": 1, "from_date": ("<=", ref_date)},
        "name",
        order_by="from_date desc",
    )
    return frappe.get_doc("Salary Structure Assignment", ssa_name) if ssa_name else None


def _get_monthly_salary_from_ssa(ssa) -> float:

    custom_total = flt(getattr(ssa, "custom_total", 0) or 0)
    base = flt(getattr(ssa, "base", 0) or 0)
    return custom_total if custom_total else base


def _days_inclusive(start_date: date, end_date: date) -> int:
    if not start_date or not end_date:
        return 0
    if end_date < start_date:
        return 0
    return (end_date - start_date).days + 1


def _is_end_of_month(d: date) -> bool:
    first_next = date(
        d.year + (1 if d.month == 12 else 0),
        (1 if d.month == 12 else d.month + 1),
        1,
    )
    last_day = first_next - timedelta(days=1)
    return d == last_day


def _get_calc_date(employee: str, transaction_date: str = None, as_of_date: str = None):
    relieving_date = frappe.db.get_value("Employee", employee, "relieving_date")
    if relieving_date:
        return getdate(relieving_date)
    if transaction_date:
        return getdate(transaction_date)
    if as_of_date:
        return getdate(as_of_date)
    return getdate(nowdate())


# -----------------------------
# Whitelisted - Validate
# -----------------------------
@frappe.whitelist()
def validate_employee_relieve_and_status(employee: str, transaction_date: str = None):
    if not employee:
        return {"ok": False, "msg": "Employee is required"}

    emp = frappe.db.get_value("Employee", employee, ["status", "relieving_date"], as_dict=True)
    if not emp:
        return {"ok": False, "msg": "Employee not found"}

    if not emp.relieving_date:
        return {"ok": False, "msg": "Relieving Date is missing"}

    status = (emp.status or "").strip().lower()
    if status == "active":
        return {"ok": False, "msg": "Employee still active"}

    return {"ok": True}


# -----------------------------
# Payroll Context
# -----------------------------
def _get_payroll_context_internal(employee: str, d: date, period_from: str = None, period_to: str = None):
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
        return {"ok": True, "years": 0, "months": 0, "days": 0, "custom_total_of_years":0.0,"date_of_joining": str(doj), "as_of_date": str(as_of_date)}

    rd = relativedelta(as_of_date, doj)
    total_years=rd.years+(rd.months/12)+(rd.days/365)
    return {
        "ok": True,
        "years": rd.years,
        "months": rd.months,
        "days": rd.days,
        "custom_total_of_years":total_years,
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
# Leave Encashment (Annual only via Allow Encashment)
# -----------------------------
def _get_leave_encashment_internal(employee: str, d: date):
    try:
        # HRMS
        from hrms.hr.doctype.leave_application.leave_application import get_leave_details
    except Exception:
        return {"ok": False, "msg": "Unable to import get_leave_details from HRMS."}

    details = get_leave_details(employee, d) or {}
    leave_allocation = details.get("leave_allocation") or {}

    if not leave_allocation and isinstance(details.get("message"), dict):
        leave_allocation = details["message"].get("leave_allocation") or {}

    if not isinstance(leave_allocation, dict) or not leave_allocation:
        return {"ok": False, "msg": f"No leave allocation data returned as of {d}."}

    # Rate per day from payroll context
    ctx = _get_payroll_context_internal(employee, d)
    rate = flt((ctx or {}).get("rate_per_day") or 0)
    if rate <= 0:
        return {"ok": False, "msg": "Rate per day is 0. Check Salary Structure Assignment/base/custom_total."}


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

    # leave_type هنا ما رح نستخدمه لأنك بدك السنوي المسموح بالصرف فقط
    return _get_leave_encashment_internal(employee, d)


@frappe.whitelist()
def get_full_and_final_payload(employee: str, transaction_date: str = None):
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

    payroll = _get_payroll_context_internal(employee, d)
    if not payroll.get("ok"):
        return {"ok": False, "msg": payroll.get("msg")}

    leave = _get_leave_encashment_internal(employee, d)

    if not leave.get("ok"):
        leave = {"ok": True, "as_of_date": str(d), "rate": payroll.get("rate_per_day", 0), "days": 0, "amount": 0, "breakdown": []}

    payables = []

    # Worked Day row
    payables.append({
        "component": "Worked Day",
        "worked_days": flt(payroll.get("worked_days") or 0),
        "rate_per_day": flt(payroll.get("rate_per_day") or 0),
        "auto_amount": flt(payroll.get("calculated_amount") or 0),
    })

    # Leave Encashment row
    payables.append({
        "component": "Leave Encashment",
        "worked_days": flt(leave.get("days") or 0),
        "rate_per_day": flt(leave.get("rate") or 0),
        "auto_amount": flt(leave.get("amount") or 0),
    })

    total_payable = 0.0
    for r in payables:
        total_payable += flt(r.get("auto_amount") or 0)

    return {
        "ok": True,
        "as_of_date": str(d),
        "service": {"years": service.get("years"), "months": service.get("months"), "days": service.get("days"),"custom_total_of_years": service.get("custom_total_of_years")},
        "payroll": payroll,
        "leave_encashment": leave,
        "payables": payables,
        "totals": {
            "leave_encashment_amount": flt(leave.get("amount") or 0),
            "total_payable": flt(total_payable),
        },
        "note": "",
    }
