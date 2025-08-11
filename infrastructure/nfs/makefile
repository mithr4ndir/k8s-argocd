# -----------------------------
# jellyfin Helm + Kustomize Make
# -----------------------------

HELM_CHART_REPO ?= https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner
HELM_VERSION    ?= 4.0.18
RELEASE_NAME    ?= nfs-subdir-external-provisioner
RELEASE_OUTPUT  := .helm-tmp-$(RELEASE_NAME)
NAMESPACE       ?= nfs
VALUES_FILE     ?= values.yaml
CHART_OUTPUT    ?= .                             # current directory
KUSTOMIZATION   ?= kustomization.yaml

# -------- Targets --------
.PHONY: all render generate-crs update-kustomize clean

all: render generate-crs update-kustomize inject-namespace

## Check for required tools
ensure-deps:
	@command -v helm >/dev/null 2>&1 || { echo "❌ helm not installed"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl not installed"; exit 1; }
	@command -v yq >/dev/null 2>&1 || { echo "❌ yq (v4+) is required for kustomization updates"; exit 1; }
	@echo "✅ All dependencies installed"

## Render Helm chart into K8s manifests
render: ensure-deps
	@echo "📦 Rendering Helm chart with $(VALUES_FILE)"
	@rm -rf $(RELEASE_OUTPUT)
	@mkdir -p $(RELEASE_OUTPUT)
	@helm template \
		$(RELEASE_NAME) \
		--repo $(HELM_CHART_REPO) \
		--version $(HELM_VERSION) \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--include-crds \
		--values $(VALUES_FILE) \
		--output-dir $(RELEASE_OUTPUT)

	@echo "📂 Flattening YAML files into current directory..."
	@find $(RELEASE_OUTPUT) -type f -name '*.yaml' -exec cp {} ./ \;
	@rm -rf $(RELEASE_OUTPUT)
	@rm -rf ./templates ./charts ./crds ./charts/crds ./charts/crds/templates

	@echo "✅ All YAMLs flattened into ./"

generate-crs:
	@echo "🧾 Generating $(NAMESPACE) config CRs..."

	@echo "---" > $(NAMESPACE)-namespace.yaml
	@echo "apiVersion: v1" >> $(NAMESPACE)-namespace.yaml
	@echo "kind: Namespace" >> $(NAMESPACE)-namespace.yaml
	@echo "metadata:" >> $(NAMESPACE)-namespace.yaml
	@echo "  name: $(NAMESPACE)" >> $(NAMESPACE)-namespace.yaml
	@echo "  labels:" >> $(NAMESPACE)-namespace.yaml
	@echo "    name: $(NAMESPACE)" >> $(NAMESPACE)-namespace.yaml
	@echo "✅ Created $(NAMESPACE)-namespace.yaml"


update-kustomize:
	@echo "🔄 Rebuilding $(KUSTOMIZATION) with kind-aware ordering..."
	@echo "resources:" > $(KUSTOMIZATION)

	@EXCLUDE="kustomization.yaml values.yaml README.md Makefile"; \
	ORDERED_KINDS="Namespace StorageClass PersistentVolume PersistentVolumeClaim CustomResourceDefinition ServiceAccount Role RoleBinding ClusterRole ClusterRoleBinding ConfigMap Secret LimitRange ResourceQuota Deployment DaemonSet StatefulSet Job CronJob Service Ingress NetworkPolicy ValidatingWebhookConfiguration MutatingWebhookConfiguration"; \
	for KIND in $$ORDERED_KINDS; do \
		for file in *.yaml; do \
			if [ -f "$$file" ] && ! echo "$$EXCLUDE" | grep -qw "$$file"; then \
				FILE_KIND=$$(yq e '.kind // ""' "$$file" 2>/dev/null | grep -v '^$$' | head -n1); \
				if [ "$$FILE_KIND" = "$$KIND" ]; then \
					echo "  - $$file" >> $(KUSTOMIZATION); \
				fi; \
			fi; \
		done; \
	done; \
	for file in *.yaml; do \
		if [ -f "$$file" ] && ! echo "$$EXCLUDE" | grep -qw "$$file"; then \
			grep -q "  - $$file" $(KUSTOMIZATION) || echo "  - $$file" >> $(KUSTOMIZATION); \
		fi; \
	done

	@echo "✅ Finished: $(KUSTOMIZATION) is now kind-ordered and deduplicated"

inject-namespace:
	@echo "📛 Injecting namespace into $(KUSTOMIZATION)..."
	@NAMESPACE=$$(yq e '.metadata.name' *namespace.yaml 2>/dev/null | grep -v null | head -n1); \
	if [ -n "$$NAMESPACE" ]; then \
		sed -i "1s|^|namespace: $$NAMESPACE\n|" $(KUSTOMIZATION); \
		echo "✅ Namespace '$$NAMESPACE' injected into $(KUSTOMIZATION)."; \
	else \
		echo "⚠️  No namespace detected in *namespace.yaml. Skipping injection."; \
	fi

## Clean rendered files
clean:
	@rm -f $(IPPOOL_FILE) $(L2ADV_FILE)
	@find . -maxdepth 1 -name '*.yaml' -not -name '$(VALUES_FILE)' -not -name '$(KUSTOMIZATION)' -exec rm -f {} +
	@echo "🧹 Cleaned up rendered files"
