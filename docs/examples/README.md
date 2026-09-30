# Example output -- all sanitized/synthetic, safe to commit

- `research-demo-sample-output.md` — exact output of
  `python3 -m omnissa_agent.cli research-demo --focus Renewal`, using
  only `research.sample_opportunities()` (every entry flagged
  `is_sample_data=True`, no real account/event/person). Regenerate any
  time with the command above; nothing here reads Gmail or the network.
- The focus-reorder example (Stage 1) is inline in
  `docs/operations-record.md` §5b and `docs/autonomous-vision-and-open-decisions.md`
  rather than duplicated as a separate file — it's a one-line brief
  header plus reordered findings, easier to read in context there.

Neither file contains real Gmail message content, a real contact, or a
real credential of any kind.
