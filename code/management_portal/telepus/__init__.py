"""Telepus: the Telegram bridge of the Portal (decision_66f5be947b67, decision_692a484e9bea).

A Telegram contact talks to its Portal User's personal assistant facade, or to
one Resident chosen by ID. The package holds the whole feature:

- ``router_service`` / ``refresh_service``: the two user services. The first
  receives Telegram updates, the second pushes replies back by event cursor.
- ``router_core``: messages, commands, question cards and ``/history``.
- ``portal_client``: every Portal call, as signed HATG ISA instructions made
  by the contact's own Portal User.
- ``telegram_adapter`` / ``telegram_client`` / ``telegram_format``: Telegram
  polling and sending, Markdown to Telegram HTML, splitting.
- ``router_store`` / ``postgres``: bridge state in the ``telepus`` schema.
- ``contacts_store``, ``instructions``, ``web_isa``, ``runtime``: the contact
  table, the Portal instructions, and the service switch.
- ``cli``: commands for the Telepus Watchboard and manual checks.
"""
