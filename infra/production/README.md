# V2 control state

This root owns only the v2 operations bucket and blue/green control parameters.
It deliberately uses `battlevive-bot/control.tfstate` and must never migrate or
import the v1 `battlevive-bot/production.tfstate`: v2 creates independent slots
and the v1 host is not reused. Bootstrap the state bucket, apply this root, and
then create the inactive slot from its own isolated state root.

The promotion workflow creates and verifies an active-slot RDS snapshot before
retiring that slot. That verified snapshot is the recovery point, so retired
slot destruction remains enabled rather than being blocked by RDS deletion
protection or an unplanned final snapshot.
