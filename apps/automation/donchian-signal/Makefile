CHART_DIR := $(HOME)/code/palantir-nq/deploy/helm/donchian-signal
NAMESPACE := automation

.PHONY: all render update-kustomize clean

all: render update-kustomize

render:
	helm template donchian-signal $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--set namespace=$(NAMESPACE) \
		> /tmp/donchian-signal-rendered.yaml
	python3 -c "import yaml; docs = [d for d in yaml.safe_load_all(open('/tmp/donchian-signal-rendered.yaml')) if d]; \
import os; \
[open({'ConfigMap':'configmap-grafana-dashboard.yaml','CronJob':'cronjob.yaml','ExternalSecret':'externalsecret.yaml'}[d['kind']], 'w').write(yaml.dump(d, sort_keys=False, default_flow_style=False)) for d in docs]"
	rm /tmp/donchian-signal-rendered.yaml

update-kustomize:
	@echo "kustomization.yaml is hand-curated to preserve resource ordering"

clean:
	rm -f cronjob.yaml externalsecret.yaml configmap-grafana-dashboard.yaml
