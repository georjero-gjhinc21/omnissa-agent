# 24x7 — designed, DISABLED until Gmail approval

Day one = on-demand only. To go continuous later (user approval required):

Option A — Orca automation (preferred, visible in Orca UI):

```bash
orca automations list
# when approved:
# orca automations create --worktree path:/home/georjero/omnissa-agent \
#   --schedule "*/30 * * * *" --task "poll label:Omnissa (readonly), summarise new"
```

Option B — systemd user timer on Spark calling a readonly poll script
that only touches `label:Omnissa` and appends to `research/inbox/`.

Never enable either before: OAuth `gmail.readonly` granted + label
verified + user says "go 24x7".
