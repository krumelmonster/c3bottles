import json

from datetime import datetime

from c3bottles import c3bottles, db


class DropPoint(db.Model):
    """A location in the venue for visitors to drop their empty bottles.

    A drop point consists of a sign "bottle drop point <number>" at the
    wall which tells visitors that a drop point should be present there
    and a number of empty crates to drop bottles into. The sign is
    resembled by the location class whereas the crates are resembled by
    the capacity class. Drop points may exist with capacity zero (i.e.
    only a sign on the wall, no crates) or a location with valid time but
    no description or coordinates (i.e. the drop point is present
    somewhere but the location is unknown).

    If the `removed` column is not null, the drop
    point has been removed from the venue completely (numbers are not
    reassigned).

    Each drop point is referenced by a unique number, which is
    consequently the primary key to identify a specific drop point. Since
    capacity and location of drop points may change over time, they are
    not simply saved in the table of drop points but rather classes
    themselves.
    """

    number = db.Column(
        db.Integer,
        primary_key=True,
        autoincrement=False)

    removed = db.Column(db.DateTime)

    locations = db.relationship(
        "Location",
        order_by="Location.start_time"
    )

    capacities = db.relationship(
        "Capacity",
        order_by="Capacity.start_time"
    )

    reports = db.relationship(
        "Report",
        lazy="dynamic"
    )

    visits = db.relationship(
        "Visit",
        lazy="dynamic"
    )

    def __init__(
            self,
            number,
            description=None,
            lat=None,
            lng=None,
            level=None,
            crates=None,
            start_time=None
    ):

        errors = []

        try:
            self.number = int(number)
        except (TypeError, ValueError):
            errors.append({"number": "Drop point number is not a number."})
        else:
            if self.number < 1:
                errors.append({"number": "Drop point number is not positive."})
            if db.session.query(DropPoint).get(self.number):
                errors.append({"number": "That drop point already exists."})

        try:
            Location(
                self,
                start_time=start_time,
                description=description,
                lat=lat,
                lng=lng,
                level=level
            )
        except ValueError as e:
            for m in e.message:
                errors.append(m)

        try:
            Capacity(
                self,
                start_time=start_time,
                crates=crates
            )
        except ValueError as e:
            for m in e.message:
                errors.append(m)

        if errors:
            raise ValueError(errors)

        db.session.add(self)

    def remove(self, time=None):

        if self.removed:
            raise RuntimeError({"DropPoint": "Drop point already removed."})

        if time and not isinstance(time, datetime):
            raise TypeError({"DropPoint": "Removal time not a datetime object."})

        if time and time > datetime.today():
            raise ValueError({"DropPoint": "Removal time in the future."})

        self.removed = time if time else datetime.today()

    def report(self, state=None, time=None):
        Report(self, time=time, state=state)

    def visit(self, action=None, time=None):
        Visit(self, time=time, action=action)

    def get_current_location(self):
        return self.locations[-1] if self.locations else None

    def get_current_crate_count(self):
        return self.capacities[-1].crates if self.capacities else 0

    def get_total_report_count(self):
        return self.reports.count()

    def get_new_report_count(self):
        last_visit = self.get_last_visit()
        if last_visit:
            return self.reports. \
                filter(Report.time > last_visit.time). \
                count()
        else:
            return self.get_total_report_count()

    def get_last_state(self):
        last_report = self.get_last_report()
        last_visit = self.get_last_visit()

        if last_report is not None and last_visit is not None:
            if last_visit.time > last_report.time \
                    and last_visit.action == "EMPTIED":
                return "EMPTY"
            else:
                return last_report.state
        elif last_report is not None:
            return last_report.state
        else:
            return "UNKNOWN"

    def get_last_report(self):
        return self.reports.order_by(Report.time.desc()).first()

    def get_last_visit(self):
        return self.visits.order_by(Visit.time.desc()).first()

    def get_new_reports(self):
        last_visit = self.get_last_visit()
        if last_visit:
            return self.reports. \
                filter(Report.time > last_visit.time). \
                order_by(Report.time.desc()). \
                all()
        else:
            return self.reports.order_by(Report.time.desc()).all()

    def get_visit_interval(self):
        """Get the visit interval for this drop point.

        This method returns the visit interval for this drop point
        in seconds.

        This is not implemented as a static method or a constant
        since in the future the visit interval might depend on
        capacity or location of drop points, time of day or a
        combination of those.
        """

        # TODO:
        # currently fixed at 2 hours (in seconds). the base interval
        # should be stored in the configuration or something.

        return 120 * 60

    def get_priority(self):
        """Get the priority to visit this drop point.

        The priority to visit a drop point mainly depends on the
        number and weight of reports since the last visit and
        the capacity of the drop point (larger: more important).

        In addition, priority increases with time since the last
        visit even if the states of reports indicate a low priority.
        This ensures that every drop point is visited from time to
        time.
        """

        # The priority of a removed drop point obviously is always 0.
        if self.removed:
            return 0

        new_reports = self.get_new_reports()

        # This is the starting priority. The report weight should
        # be scaled relative to 1, so this can be interpreted as a
        # number of standing default reports ensuring that every
        # drop point's priority increases slowly if it is not
        # visited even if no real reports come in.
        priority = 1

        for report in new_reports:
            priority += report.get_weight()

        if self.get_current_crate_count() >= 1:
            priority *= (1 + 0.1 * self.get_current_crate_count())

        if self.get_last_visit():
            priority *= (datetime.today() -
                         self.get_last_visit().time).total_seconds() \
                        / self.get_visit_interval()
        else:
            priority *= 3

        return round(priority, 2)

    @staticmethod
    def get(number):
        try:
            return db.session.query(DropPoint).get(number)
        except TypeError:
            return None

    @staticmethod
    def get_all_dps_as_geojson():
        """Get all drop points as a GeoJSON string."""

        arr = []

        for dp in db.session.query(DropPoint).all():
            arr.append({
                "type": "Feature",
                "properties": {
                    "number": dp.number,
                    "description": dp.get_current_location().description,
                    "reports_total": dp.get_total_report_count(),
                    "reports_new": dp.get_new_report_count(),
                    "priority": dp.get_priority(),
                    "last_state": dp.get_last_state(),
                    "crates": dp.get_current_crate_count(),
                    "removed": True if dp.removed else False
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        dp.get_current_location().lng,
                        dp.get_current_location().lat
                    ]
                }
            })

        return json.dumps(arr, indent=4 if c3bottles.debug else None)

    def __repr__(self):
        return "Drop point %s (%s)" % (
            self.number,
            "inactive" if self.removed else "active"
        )


class Location(db.Model):
    """A physical location of a drop point at some point in time.

    Drop points may be relocated at any time for whatever reason. For
    analysis after an event and optimization of the drop point locations
    for the next event at the same venue, drop point locations are tracked
    over time.

    Each location has a start time indicating the placement of the drop
    point at that location. If a drop point is relocated, a new location
    with the respective start time is added. If the start time is null,
    the drop point has been there since the creation of the universe.

    If the human-readable description as well as the coordinates both are
    null, the location of that drop point is unknwon.
    """

    max_description = 140

    loc_id = db.Column(db.Integer, primary_key=True)

    dp_id = db.Column(
        db.Integer,
        db.ForeignKey("drop_point.number"),
        nullable=False
    )

    dp = db.relationship("DropPoint")

    start_time = db.Column(db.DateTime)
    description = db.Column(db.String(max_description))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    level = db.Column(db.Float)

    def __init__(
            self,
            dp,
            start_time=None,
            description=None,
            lat=None,
            lng=None,
            level=None
    ):

        errors = []

        if not isinstance(dp, DropPoint):
            errors.append({"Location": "Not given a drop point object."})

        self.dp = dp

        if start_time and not isinstance(start_time, datetime):
            errors.append({"Location": "Start time not a datetime object."})

        if start_time and start_time > datetime.today():
            errors.append({"Location": "Start time in the future."})

        if dp.locations and start_time and \
                start_time < dp.locations[-1].start_time:
            errors.append({"Location": "Location older than current."})

        self.start_time = start_time if start_time else datetime.today()

        try:
            self.lat = float(lat)
        except (TypeError, ValueError):
            errors.append({"lat": "Latitude is not a floating point number."})
        else:
            if not -90 < self.lat < 90:
                errors.append({"lat": "Latitude is not between 90 degrees N/S."})

        try:
            self.lng = float(lng)
        except (TypeError, ValueError):
            errors.append({"lng": "Longitude is not a floating point number."})
        else:
            if not -180 < self.lng < 180:
                errors.append({"lat": "Longitude is not between 180 degrees W/E."})

        try:
            self.level = int(level)
        except (TypeError, ValueError):
            errors.append({"level": "Building level is not a number."})

        try:
            self.description = str(description)
        except (TypeError, ValueError):
            errors.append({"description": "Room description is not a string."})
        else:
            if len(self.description) > self.max_description:
                errors.append({"description": "Room description is too long."})

        if errors:
            raise ValueError(errors)

        db.session.add(self)

    def __repr__(self):
        return "Location %s of drop point %s (%s since %s)" % (
            self.loc_id, self.dp_id,
            self.description, self.start_time
        )


class Capacity(db.Model):
    """The capacity of a drop point at some point in time.

    The capacity of drop points may change over time as empty crates can
    and will be added or removed on demand. Like the location, the
    capacity is tracked over time to allow for analysis and optimization
    after an event.

    Each capacity has a start time indicating the presence of a particular
    number of empty crates at the drop point at that time. If crates are
    added or removed, a new capacity with the respective start time is
    added. If the start time is null, the crates have been there forever.

    If the number of crates is null, the drop point only consists of a
    sign on the wall but no crates at all.
    """

    default_crate_count = 1

    cap_id = db.Column(db.Integer, primary_key=True)

    dp_id = db.Column(
        db.Integer,
        db.ForeignKey("drop_point.number"),
        nullable=False
    )

    dp = db.relationship("DropPoint")

    start_time = db.Column(db.DateTime)
    crates = db.Column(db.Integer, default=default_crate_count)

    def __init__(
            self,
            dp,
            start_time=None,
            crates=default_crate_count
    ):

        errors = []

        if not isinstance(dp, DropPoint):
            errors.append({"Capacity": "Not given a drop point object."})

        self.dp = dp

        if start_time and not isinstance(start_time, datetime):
            errors.append({"Capacity": "Start time not a datetime object."})

        if start_time and start_time > datetime.today():
            errors.append({"Capacity": "Start time in the future."})

        if dp.capacities and start_time and \
                start_time < dp.capacities[-1].start_time:
            errors.append({"Capacity": "Capacity older than current."})

        self.start_time = start_time if start_time else datetime.today()

        if crates is None:
            crates = self.default_crate_count
        else:
            try:
                self.crates = int(crates)
            except (TypeError, ValueError):
                errors.append({"crates": "Crate count is not a number."})
            else:
                if self.crates < 0:
                    errors.append({"crates": "Crate count is not positive."})

        if errors:
            raise ValueError(errors)

        db.session.add(self)

    def __repr__(self):
        return "Capacity %s of drop point %s (%s crates since %s)" % (
            self.cap_id, self.dp_id,
            self.crates, self.start_time
        )


report_states = (
    "DEFAULT",
    "NO_CRATES",
    "EMPTY",
    "SOME_BOTTLES",
    "REASONABLY_FULL",
    "FULL",
    "OVERFLOW"
)


class Report(db.Model):
    """The report of a visitor that a drop point needs maintenance.

    When visitors find a drop point needing maintenance, they may report
    the drop point to the bottle collectors. A report is issued for a
    given drop point which has a time and optionally some information
    about the state of the drop point in question.
    """

    rep_id = db.Column(db.Integer, primary_key=True)

    dp_id = db.Column(
        db.Integer,
        db.ForeignKey("drop_point.number"),
        nullable=False
    )

    dp = db.relationship("DropPoint")

    time = db.Column(db.DateTime, nullable=False)

    state = db.Column(
        db.Enum(*report_states, name="report_states"),
        default=report_states[0]
    )

    def __init__(self, dp, time=None, state=None):

        errors = []

        self.dp = dp

        if not isinstance(dp, DropPoint):
            errors.append({"Report": "Not given a drop point object."})

        if time and not isinstance(time, datetime):
            errors.append({"Report": "Time not a datetime object."})

        if time and time > datetime.today():
            errors.append({"Report": "Start time in the future."})

        self.time = time if time else datetime.today()

        if state in report_states:
            self.state = state
        else:
            errors.append({"Report": "Invalid or missing reported state."})

        if errors:
            raise ValueError(errors)

        db.session.add(self)

    def get_weight(self):
        """Get the weight (i.e. significance) of a report.

        The weight of a report determines how significant it is for the
        calculation of the priority to visit the respective drop point
        soon.

        Most important for the weight of a report is the state of the
        drop point as seen by the reporter. The report of an
        overflowing drop point is certainly more important than one
        of a drop point nearly empty.

        If the reporter is a trusted user, that increases the weight.

        Special users see special weights: The supervisor of the
        bottle collectors is not focused on full drop points (that's
        what they have a collector team for) but rather on solving
        problems like overflows or missing crates reported by trusted
        users.

        The default weight under default conditions is 1 and all
        influences should only multiply that default value with some
        factor.
        """

        # TODO:
        # - weight should depend on the state (OVERFLOW > FULL > rest)
        # - weight should depend on the reporter (trusted > stranger)
        # - weight should depend on the viewer (supervisor: problem-
        #   focused, collector: collection-focused)

        return 1

    def __repr__(self):
        return "Report %s of drop point %s (state %s at %s)" % (
            self.rep_id, self.dp_id,
            self.state, self.time
        )


visit_actions = (
    "EMPTIED",
    "ADDED_CRATE",
    "REMOVED_CRATE",
    "RELOCATED",
    "REMOVED",
    "NO_ACTION"
)


class Visit(db.Model):
    """A maintenance visit of bottle collectors at a drop point.

    After a report of a problem with a certain drop point has been
    generated or a drop point has not been visited for a long time, the
    bottle collectors are advised to visit that drop point. After doing
    so, they log their visit and the action taken.
    """

    vis_id = db.Column(db.Integer, primary_key=True)

    dp_id = db.Column(
        db.Integer,
        db.ForeignKey("drop_point.number"),
        nullable=False
    )

    dp = db.relationship("DropPoint")

    time = db.Column(db.DateTime, nullable=False)

    action = db.Column(
        db.Enum(*visit_actions, name="visit_actions"),
        default=visit_actions[0]
    )

    def __init__(self, dp, time=None, action=None):

        errors = []

        self.dp = dp

        if not isinstance(dp, DropPoint):
            errors.append({"Visit": "Not given a drop point object."})

        if time and not isinstance(time, datetime):
            errors.append({"Visit": "Time not a datetime object."})

        if time and time > datetime.today():
            errors.append({"Visit": "Start time in the future."})

        self.time = time if time else datetime.today()

        if action in visit_actions:
            self.action = action
        else:
            errors.append({"Visit": "Invalid or missing maintenance action."})

        if errors:
            raise ValueError(errors)

        db.session.add(self)

    def __repr__(self):
        return "Visit %s of drop point %s (action %s at %s)" % (
            self.vis_id, self.dp_id,
            self.action, self.time
        )

# vim: set expandtab ts=4 sw=4:
