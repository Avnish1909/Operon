"""Northwind Supply Co. — a simulated company for the operator to work in.

The sandbox is deliberately *not* part of the agent. It plays the role of the
company's existing systems (a legacy ERP with no API, a helpdesk with a REST API,
an HR portal, an internal mail service) plus a shared file drive. The agent only
ever reaches it the way it would reach real systems: through a browser, HTTP
APIs and files.
"""
