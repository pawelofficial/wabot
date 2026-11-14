"""Treningi: the trainee's training calendar, a month at a time (?month=2026-10), and below it
two tabs: the month's trainings with everything reported (docs/PLAN.md, "Training calendar"),
and Ćwiczenia (?view=exercises): every exercise done, a summary per exercise or the history
of the chosen ones (&exercise=back_squat, repeated for several), over a date range
(&from=…&to=…, the whole history by default).

A day shows its current trainings (queries.workouts()): the plan's, coloured by its status and
labelled with its plan day's letter, and one outside the plan. Clicking a day jumps to its
details further down the page; no script is needed.
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
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


# Months the calendar shows; anything else in ?month= is treated like a value that isn't a month
# (the calendar's weeks and the ‹ › links would leave the years a date can have).
FIRST_YEAR, LAST_YEAR = 2000, 2100


def month_asked(value: str | None, today: dt.date) -> dt.date:
    """The first day of the month in ?month=2026-10; this month without one, if it's not a month,
    or if it's outside FIRST_YEAR–LAST_YEAR."""
    try:
        month = dt.date.fromisoformat(f"{value}-01")
    except (TypeError, ValueError):
        return today.replace(day=1)
    return month if FIRST_YEAR <= month.year <= LAST_YEAR else today.replace(day=1)


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
    view: str | None = None,
    exercise: Annotated[list[str] | None, Query()] = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
):
    """The calendar of one month and, below it, that month's trainings or the exercises."""
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
        view="exercises" if view == "exercises" else "trainings",
        ex=exercise_view(session, trainee, current, exercise or [], from_, to)
        if view == "exercises"
        else None,
    )


# Polish alphabetical order (ą after a, ł after l, ś after s, ...): Python's own puts them after z.
POLISH = "aąbcćdeęfghijklłmnńoópqrsśtuvwxyzźż"


def polish_key(text: str) -> list[int]:
    return [POLISH.index(c) if c in POLISH else 100 + ord(c) for c in text.casefold()]


def date_asked(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass
class ExerciseView:
    """What the Ćwiczenia tab shows."""

    # (key, name): every exercise done and the current plan's others, alphabetically.
    options: list[tuple[str, str]]
    chosen: list[str]  # option keys, in the options' order; none: the summary of all
    first: dt.date | None  # the range asked (None: from the first training)
    last: dt.date | None
    earliest: dt.date | None  # the trainee's first and last training with exercises
    latest: dt.date | None
    summary: list[queries.ExerciseSummary]  # all exercises in the range (nothing chosen)
    rows: list[queries.ExerciseDone]  # the chosen exercises', newest first
    stats: list[queries.ExerciseSummary]  # each chosen exercise's done in the range

    @property
    def chosen_names(self) -> list[str]:
        names = dict(self.options)
        return [names[key] for key in self.chosen]


def exercise_view(session, trainee, plan, asked: list[str], from_, to) -> ExerciseView:
    everything = queries.exercises_done(session, trainee)
    options = [(s.key, s.name) for s in queries.summarize(everything)]
    known = {key for key, _ in options}
    for day in plan.days if plan else []:
        for planned in day.exercises:
            if planned.id not in known:
                known.add(planned.id)
                options.append((planned.id, f"{exercises.BY_ID[planned.id].name} (jeszcze nie)"))
    options.sort(key=lambda option: polish_key(option[1]))
    first, last = date_asked(from_), date_asked(to)
    done = [
        d
        for d in everything
        if (first is None or d.date >= first) and (last is None or d.date <= last)
    ]
    chosen = [key for key, _ in options if key in set(asked)]
    rows = [d for d in done if queries.exercise_key(d.exercise) in chosen]
    return ExerciseView(
        options=options,
        chosen=chosen,
        first=first,
        last=last,
        earliest=min((d.date for d in everything), default=None),
        latest=max((d.date for d in everything), default=None),
        summary=[] if chosen else queries.summarize(done),
        rows=rows,
        stats=queries.summarize(rows),
    )


def _labelled(plan: plans.TrainingPlan | None) -> list[tuple[plans.PlanDay, str]]:
    """The current plan's days with their letters, for the legend."""
    if plan is None:
        return []
    labels = plan.day_labels()
    return [(d, labels[d.id]) for d in plan.days if d.id in labels]
