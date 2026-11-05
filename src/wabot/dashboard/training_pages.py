"""Treningi: the trainee's training calendar, a month at a time (?month=2026-10), and below it
the month's trainings with everything reported (docs/PLAN.md, "Training calendar").

A day shows its current trainings (queries.workouts()): the plan's, coloured by its status and
labelled with its plan day's letter, and one outside the plan. Clicking a day jumps to its
details further down the page; no script is needed.
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from wabot import exercises, plans
from wabot.dashboard import queries
from wabot.dashboard.auth import SessionDep, TraineeDep, TrainerDep
from wabot.dashboard.queries import Workout
from wabot.dashboard.routes import render

router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)

# Workout.kind values, in the order the tiles and the legend show them.
KINDS = ("done", "partial", "skipped", "outside")


@dataclass
class CalendarDay:
    date: dt.date
    in_month: bool  # False for the days of the next and previous month that fill the weeks
    workouts: list[Workout] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return f"d-{self.date.isoformat()}"

    @property
    def summary(self) -> str:
        return "; ".join(w.summary for w in self.workouts)


@dataclass
class Week:
    days: list[CalendarDay]

    @property
    def from_plan(self) -> int:
        """Trainings of the plan done that week, fully or partly."""
        return sum(w.kind in ("done", "partial") for d in self.days for w in d.workouts)


def month_asked(value: str | None, today: dt.date) -> dt.date:
    """The first day of the month in ?month=2026-10; this month without one or if it's not a
    month."""
    try:
        return dt.date.fromisoformat(f"{value}-01")
    except (TypeError, ValueError):
        return today.replace(day=1)


def shift(month: dt.date, months: int) -> dt.date:
    """The first day of the month `months` later (earlier if negative)."""
    index = month.year * 12 + month.month - 1 + months
    return dt.date(index // 12, index % 12 + 1, 1)


def month_param(month: dt.date) -> str:
    return f"{month:%Y-%m}"


@router.get("/trainees/{trainee_id}/trainings")
def trainings(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    month: str | None = None,
):
    """The calendar of one month and that month's trainings."""
    # Dates are the trainee's: a training's date is the day in their timezone.
    today = dt.datetime.now(ZoneInfo(trainee.timezone)).date()
    first = month_asked(month, today)
    # Whole weeks, Monday first, so each week's count includes its days in the next month.
    dates = calendar.Calendar().monthdatescalendar(first.year, first.month)
    found = queries.workouts(session, trainee, dates[0][0], dates[-1][-1])
    by_date: dict[dt.date, list[Workout]] = {}
    for w in found:
        by_date.setdefault(w.checkin.date, []).append(w)
    weeks = [
        Week([CalendarDay(d, d.month == first.month, by_date.get(d, [])) for d in week])
        for week in dates
    ]
    # (The weeks span six weeks at most, so the month alone tells the shown month's days.)
    in_month = [w for w in found if w.checkin.date.month == first.month]
    days = [d for week in weeks for d in week.days if d.in_month and d.workouts]
    counts = {kind: sum(w.kind == kind for w in in_month) for kind in KINDS}
    current = plans.of(trainee)
    last = queries.last_training_date(session, trainee)
    return render(
        request,
        "trainings.html",
        session,
        trainer,
        trainee,
        "trainings",
        today=today,
        month=first,
        weeks=weeks,
        days=list(reversed(days)),  # newest first
        counts=counts,
        last=last,
        legend=[(label, d.name) for d, label in _labelled(current)],
        previous=month_param(shift(first, -1)),
        next=month_param(shift(first, 1)) if first < today.replace(day=1) else None,
        month_param=month_param,
        catalog=exercises.BY_ID,
    )


def _labelled(plan: plans.TrainingPlan | None) -> list[tuple[plans.PlanDay, str]]:
    """The current plan's days with their letters, for the legend."""
    if plan is None:
        return []
    labels = plan.day_labels()
    return [(d, labels[d.id]) for d in plan.days if d.id in labels]
