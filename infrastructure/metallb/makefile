# -----------------------------
# MetalLB Helm + Kustomize Make
# -----------------------------

HELM_CHART_REPO ?= https://metallb.github.io/metallb
HELM_VERSION    ?= 0.15.2
RELEASE_NAME    ?= metallb
RELEASE_OUTPUT  := .helm-tmp-$(RELEASE_NAME)
NAMESPACE       ?= metallb-system
VALUES_FILE     ?= values.yaml
CHART_OUTPUT    ?= .                             # current directory
KUSTOMIZATION   ?= kustomization.yaml
IPPOOL_FILE     ?= ipaddresspool.yaml
L2ADV_FILE      ?= l2advertisement.yaml
IPPOOL_CIDR     ?= 192.168.1.225-192.168.1.240
INTERFACES      ?= eth0                          # leave blank to avoid eth0 issues

# -------- Targets --------
.PHONY: all render generate-crs update-kustomize clean

all: render generate-crs update-kustomize

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
	@echo "🧾 Generating MetalLB config CRs..."
	@echo "---" > $(IPPOOL_FILE)
	@echo "apiVersion: metallb.io/v1beta1" >> $(IPPOOL_FILE)
	@echo "kind: IPAddressPool" >> $(IPPOOL_FILE)
	@echo "metadata:" >> $(IPPOOL_FILE)
	@echo "  name: local-pool" >> $(IPPOOL_FILE)
	@echo "  namespace: $(NAMESPACE)" >> $(IPPOOL_FILE)
	@echo "spec:" >> $(IPPOOL_FILE)
	@echo "  addresses:" >> $(IPPOOL_FILE)
	@echo "    - $(IPPOOL_CIDR)" >> $(IPPOOL_FILE)

	@echo "---" > $(L2ADV_FILE)
	@echo "apiVersion: metallb.io/v1beta1" >> $(L2ADV_FILE)
	@echo "kind: L2Advertisement" >> $(L2ADV_FILE)
	@echo "metadata:" >> $(L2ADV_FILE)
	@echo "  name: l2" >> $(L2ADV_FILE)
	@echo "  namespace: $(NAMESPACE)" >> $(L2ADV_FILE)
	@echo "spec: {}" >> $(L2ADV_FILE)
	@echo "✅ Created $(IPPOOL_FILE) and $(L2ADV_FILE)"

	@echo "---" > metallb-namespace.yaml
	@echo "apiVersion: v1" >> metallb-namespace.yaml
	@echo "kind: Namespace" >> metallb-namespace.yaml
	@echo "metadata:" >> metallb-namespace.yaml
	@echo "  name: $(NAMESPACE)" >> metallb-namespace.yaml
	@echo "  labels:" >> metallb-namespace.yaml
	@echo "    name: $(NAMESPACE)" >> metallb-namespace.yaml
	@echo "✅ Created metallb-namespace.yaml"


update-kustomize:
	@echo "🔄 Rebuilding $(KUSTOMIZATION) with kind-aware ordering..."
	@echo "resources:" > $(KUSTOMIZATION)

	@EXCLUDE="kustomization.yaml values.yaml README.md Makefile"; \
	ORDERED_KINDS="Namespace CustomResourceDefinition ServiceAccount Role RoleBinding ClusterRole ClusterRoleBinding ConfigMap Secret LimitRange ResourceQuota Deployment DaemonSet StatefulSet Job CronJob Service Ingress NetworkPolicy ValidatingWebhookConfiguration MutatingWebhookConfiguration"; \
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


## Clean rendered files
clean:
	@rm -f $(IPPOOL_FILE) $(L2ADV_FILE)
	@find . -maxdepth 1 -name '*.yaml' -not -name '$(VALUES_FILE)' -not -name '$(KUSTOMIZATION)' -exec rm -f {} +
	@echo "🧹 Cleaned up rendered files"
