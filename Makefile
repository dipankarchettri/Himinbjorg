VENV    := .venv
PY      := $(VENV)/bin/python
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: venv up down logs status agent test proof bench gate-check extension extension-test \
        scenario-failover scenario-budget scenario-privacy scenario-approval clean

venv:
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -q -r requirements.txt && $(VENV)/bin/pip install -q -e .

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f bifrost

status:
	@$(COMPOSE) ps
	@echo
	@curl -s -H "x-bf-vk: $$(grep '^BIFROST_VK_DEMO' .env | cut -d= -f2)" \
		http://localhost:8099/api/governance/virtual-keys/quota | head -c 300; echo

# Run the agent. Approval prompts need a real terminal.
#   make agent TASK="fix divide()" REPO=bench/seed
agent:
	$(PY) -m agent.main "$(TASK)" --repo $(or $(REPO),.)

test:
	$(PY) -m pytest bench/test_security.py bench/test_write.py bench/test_tasks.py bench/test_loop.py bench/test_server.py -q

# Check the approval prompt itself, with no model involved.
gate-check:
	@$(PY) bench/gate_check.py

bench:
	$(PY) bench/benchmark.py --tasks $(or $(TASKS),all)

bench-attribution:
	$(PY) bench/benchmark.py --tasks all --attribution

scenario-failover:
	$(PY) bench/failover_scenario.py

scenario-budget:
	$(PY) bench/budget_scenario.py

scenario-privacy:
	$(PY) bench/privacy_scenario.py

scenario-approval:
	$(PY) bench/approval_scenario.py

# Everything, into one timestamped evidence directory.
proof:
	$(PY) bench/proof.py

clean:
	rm -rf .bench-work traces/bench-*.jsonl

scenario-cache:
	$(PY) bench/cache_scenario.py add_power

# VS Code extension: build extension/himinbjorg.vsix, then
#   code --install-extension extension/himinbjorg.vsix
extension:
	cd extension && npm ci --no-audit --no-fund && npm run package

# End to end against the real agent (needs `make up`), then inside a
# downloaded VS Code (needs a display).
extension-test:
	cd extension && npm test && npm run test:vscode
