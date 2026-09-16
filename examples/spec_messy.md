# Warehouse bot — draft spec

The warehouse bot monitors storage and places reorders automatically.

## Rules

1. When usage is high, scale up the cluster.
2. If a delivery is late, notify the customer.
3. The retention policy is TBD — we'll decide later.
4. When the temperature is excessive, the cooling handler must be called.
5. Pass every inbound order to the connector before saving it.
6. The system should probably retry failed jobs, depending on the failure.
7. Notify staff when the queue length is very long ({{threshold}}).
8. If over quota, warn the account owner.
9. Assume three delivery partners are enough for now.
10. When the user reports a bug, create a ticket.

## Constraints

- Budget: we might spend something like 2000€ a month, maybe less.
- The reporting module should aggregate volumes, times, and costs.