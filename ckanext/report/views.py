import datetime
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


def view_org(report_name, organization, refresh=False):
    return view(report_name, organization, refresh)


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
            response.data = make_csv_from_dicts(data["table"])
        elif format == "json":
            response.headers["Content-Type"] = "application/json"
            data["generated_at"] = report_date
            response.data = json.dumps(data)
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
    "/report/<report_name>/<organization>", view_func=view_org, methods=("POST", "GET")
)




def make_csv_from_dicts(rows):
    import csv
    import io

    csvout = io.StringIO()
    csvwriter = csv.writer(
        csvout,
        dialect='excel',
        quoting=csv.QUOTE_NONNUMERIC
    )
    # extract the headers by looking at all the rows and
    # get a full list of the keys, retaining their ordering
    headers_ordered = []
    headers_set = set()
    for row in rows:
        new_headers = set(row.keys()) - headers_set
        headers_set |= new_headers
        for header in row.keys():
            if header in new_headers:
                headers_ordered.append(header)
    csvwriter.writerow(headers_ordered)
    for row in rows:
        items = []
        for header in headers_ordered:
            item = row.get(header, 'no record')
            if isinstance(item, datetime.datetime):
                item = item.strftime('%Y-%m-%d %H:%M')
            elif isinstance(item, (int, float, list, tuple)):
                item = str(item)
            elif item is None:
                item = ''
            else:
                item = item.encode('utf8')
            items.append(item)
        try:
            csvwriter.writerow(items)
        except Exception as e:
            raise Exception("%s: %s, %s" % (e, row, items))
    csvout.seek(0)
    return csvout.read()


def ensure_data_is_dicts(data):
    '''Ensure that the data is a list of dicts, rather than a list of tuples
    with column names, as sometimes is the case. Changes it in place'''
    if data['table'] and isinstance(data['table'][0], (list, tuple)):
        new_data = []
        columns = data['columns']
        for row in data['table']:
            new_data.append(OrderedDict(zip(columns, row)))
        data['table'] = new_data
        del data['columns']


def anonymise_user_names(data, organization=None):
    '''Ensure any columns with names in are anonymised, unless the current user
    has privileges.

    NB this is only enabled for data.gov.uk - it is custom functionality.
    '''
    try:
        import ckanext.dgu.lib.helpers as dguhelpers
    except ImportError:
        # If this is not DGU then cannot do the anonymization
        return
    column_names = data['table'][0].keys() if data['table'] else []
    for col in column_names:
        if col.lower() in ('user', 'username', 'user name', 'author'):
            for row in data['table']:
                row[col] = dguhelpers.user_link_info(
                    row[col], organization=organization)[0]
