# Progress — worker_m5_deployment

Last visited: 2026-09-17T00:26:15Z

## Status: Starting Investigation & Deployment Planning

### Steps:
- [x] Initialized DISPATCH.md, BRIEFING.md, progress.md
- [ ] Read ORIGINAL_REQUEST.md, PROJECT.md, and survey_r4_r6.md
- [ ] Check local deployment tools, scripts, and services (deploy/, tools/sync.sh, quant/hft/guard.py)
- [ ] Test SSH connectivity to VPS (82.115.21.155 / stratton)
- [ ] Inspect existing state on VPS
- [ ] Update / verify tools/sync.sh and synchronize codebase to /root/ict_sniper
- [ ] Install llama-server prebuilt Linux x64 binary and sub-3B model on VPS
- [ ] Configure and activate stratton-llm-critic.service
- [ ] Verify llama-server health & inference
- [ ] Check total VPS RAM consumption (must be <= 2.5 GB)
- [ ] Verify tbt-hl-hft.service and live ticks in /root/ict_sniper/data/state/hft.json
- [ ] Ensure quant/hft/guard.py monitors stratton-llm-critic.service and <= 2.5 GB memory limit
- [ ] Run test suite / verification checks
- [ ] Generate comprehensive handoff.md and report to parent
