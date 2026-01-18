CHART_URI       ?= oci://ghcr.io/bjw-s/helm/app-template
CHART_VERSION   ?= 3.5.1
RELEASE_NAME    ?= bazarr
RELEASE_OUTPUT  := .helm-tmp-$(RELEASE_NAME)
NAMESPACE       ?= media
VALUES_FILE     ?= values.yaml
KUSTOMIZATION   ?= kustomization.yaml

.PHONY: all render update-kustomize clean ensure-deps
all: render update-kustomize

ensure-deps:
	@command -v helm >/dev/null 2>&1 || { echo "❌ helm not installed"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl not installed"; exit 1; }
	@command -v yq >/dev/null 2>&1 || { echo "❌ yq (v4+) is required"; exit 1; }
	@echo "✅ All dependencies installed"

render:
	@echo "📦 Rendering Helm chart with $(VALUES_FILE)"
	@rm -rf $(RELEASE_OUTPUT)
	@mkdir -p $(RELEASE_OUTPUT)
	@helm template $(RELEASE_NAME) $(CHART_URI) --version $(CHART_VERSION) --namespace $(NAMESPACE) --include-crds --values $(VALUES_FILE) --output-dir $(RELEASE_OUTPUT)
	@echo "📂 Flattening YAML files into current directory..."
	@find $(RELEASE_OUTPUT) -type f -name '*.yaml' -exec cp {} ./ \;
	@rm -rf $(RELEASE_OUTPUT) ./templates ./charts ./crds ./charts/crds ./charts/crds/templates
	@echo "✅ All YAMLs flattened into ./"

update-kustomize:
	@echo "resources:" > $(KUSTOMIZATION)
	@EXCLUDE="kustomization.yaml values.yaml README.md Makefile"; \
	ORDERED_KINDS="Namespace StorageClass PersistentVolume PersistentVolumeClaim CustomResourceDefinition ServiceAccount Role RoleBinding ClusterRole ClusterRoleBinding ConfigMap Secret LimitRange ResourceQuota Deployment DaemonSet StatefulSet Job CronJob Service Ingress NetworkPolicy ValidatingWebhookConfiguration MutatingWebhookConfiguration"; \
	for KIND in $$ORDERED_KINDS; do \
		for file in *.yaml; do \
			if [ -f "$$file" ] && ! echo "$$EXCLUDE" | grep -qw "$$file"; then \
				FILE_KIND=$$(yq e '.kind // ""' "$$file" 2>/dev/null | grep -v '^$$' | head -n1); \
				if [ "$$FILE_KIND" = "$$KIND" ]; then echo "  - $$file" >> $(KUSTOMIZATION); fi; \
			fi; \
		done; \
	done; \
	for file in *.yaml; do \
		if [ -f "$$file" ] && ! echo "$$EXCLUDE" | grep -qw "$$file"; then \
			grep -q "  - $$file" $(KUSTOMIZATION) || echo "  - $$file" >> $(KUSTOMIZATION); \
		fi; \
	done
	@echo "✅ Kustomization updated"

clean:
	@find . -maxdepth 1 -name '*.yaml' -not -name '$(VALUES_FILE)' -not -name '$(KUSTOMIZATION)' -exec rm -f {} +
	@echo "🧹 Cleaned up rendered files"
