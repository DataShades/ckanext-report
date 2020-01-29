import ckan.plugins.toolkit as tk
from ckan.lib.helpers import json
from ckan.lib.render import TemplateNotFound
from flask import Blueprint, make_response

import ckanext.report.helpers as helpers
from ckanext.report.report_registry import Report


log = __import__("logging").getLogger(__name__)

report = Blueprint("report", __name__)


def get_blueprints():
    return [report]


def index():
    "reports"
    try:
        reports = tk.get_action("report_list")({}, {})
    except ttk.NotAuthorized:
        return tk.abort(401)

    return tk.render("report/index.html", extra_vars={"reports": reports})


def view(report_name, organization=None, refresh=False):
    "report-org"
    try:
        report = tk.get_action("report_show")({}, {"id": report_name})
    except tk.NotAuthorized:
        tk.abort(401)
    except tk.ObjectNotFound:
        tk.abort(404)

    # ensure correct url is being used
    if (
        "organization" == tk.c.controller
        and "organization" not in report["option_defaults"]
    ):
        return tk.redirect_to(helpers.relative_url_for(organization=None))
    elif (
        "organization" != tk.c.controller
        and "organization" in report["option_defaults"]
        and report["option_defaults"]["organization"]
    ):
        org = report["option_defaults"]["organization"]
        return tk.redirect_to(helpers.relative_url_for(organization=org))
    if "organization" in tk.request.args:
        # organization should only be in the url - let the param overwrite
        # the url.
        return tk.redirect_to(helpers.relative_url_for())

    # options
    options = Report.add_defaults_to_options(tk.request.args, report["option_defaults"])
    option_display_params = {}
    if "format" in options:
        format = options.pop("format")
    else:
        format = None
    if "organization" in report["option_defaults"]:
        options["organization"] = organization
    options_html = {}
    tk.c.options = options  # for legacy genshi snippets
    for option in options:
        if option not in report["option_defaults"]:
            # e.g. 'refresh' param
            log.warn(
                "Not displaying report option HTML for param %s as option not recognized"
            )
            continue
        option_display_params = {
            "value": options[option],
            "default": report["option_defaults"][option],
        }
        try:
            options_html[option] = tk.render_snippet(
                "report/option_%s.html" % option, data=option_display_params
            )
        except TemplateNotFound:
            log.warn(
                "Not displaying report option HTML for param %s as no template found"
            )
            continue

    # Alternative way to refresh the cache - not in the UI, but is
    # handy for testing
    try:
        refresh = tk.asbool(tk.request.args.get("refresh"))
        if "refresh" in options:
            options.pop("refresh")
    except ValueError:
        refresh = False

    # Refresh the cache if requested
    if tk.request.method == "POST" and not format:
        refresh = True

    if refresh:
        try:
            tk.get_action("report_refresh")({}, {"id": report_name, "options": options})
        except tk.NotAuthorized:
            tk.abort(401)
        # Don't want the refresh=1 in the url once it is done
        return tk.redirect_to(helpers.relative_url_for(refresh=None))

    # Check for any options not allowed by the report
    for key in options:
        if key not in report["option_defaults"]:
            tk.abort(400, "Option not allowed by report: %s" % key)

    try:
        data, report_date = tk.get_action("report_data_get")(
            {}, {"id": report_name, "options": options}
        )
    except tk.ObjectNotFound:
        tk.abort(404)
    except tk.NotAuthorized:
        tk.abort(401)

    response = make_response()
    if format and format != "html":
        ensure_data_is_dicts(data)
        anonymise_user_names(data, organization=options.get("organization"))
        if format == "csv":
            try:
                key = tk.get_action("report_key_get")(
                    {}, {"id": report_name, "options": options}
                )
            except tk.NotAuthorized:
                tk.abort(401)
            filename = "report_%s.csv" % key
            response.headers["Content-Type"] = "application/csv"
            response.headers["Content-Disposition"] = str(
                "attachment; filename=%s" % (filename)
            )
            response.content = make_csv_from_dicts(data["table"])
        elif format == "json":
            response.headers["Content-Type"] = "application/json"
            data["generated_at"] = report_date
            response.content = json.dumps(data)
        else:
            tk.abort(400, "Format not known - try html, json or csv")
        return response

    are_some_results = bool(data["table"] if "table" in data else data)
    # A couple of context variables for legacy genshi reports
    tk.c.data = data
    tk.c.options = options
    return tk.render(
        "report/view.html",
        extra_vars={
            "report": report,
            "report_name": report_name,
            "data": data,
            "report_date": report_date,
            "options": options,
            "options_html": options_html,
            "report_template": report["template"],
            "are_some_results": are_some_results,
        },
    )


def redirect_reports():
    return tk.redirect_to("report.index")


report.add_url_rule("/report", view_func=index)

report.add_url_rule("/reports", view_func=redirect_reports)

report.add_url_rule("/report/<report_name>", view_func=view, methods=("POST", "GET"))
report.add_url_rule(
    "/report/<report_name>/<organization>", view_func=view, methods=("POST", "GET")
)
