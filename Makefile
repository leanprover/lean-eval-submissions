.PHONY: build-UnwrapFunction

build-UnwrapFunction:
	cp scripts/aws_key_adapter.py "$(ARTIFACTS_DIR)/aws_key_adapter.py"
	cp scripts/key_capability_contract.py "$(ARTIFACTS_DIR)/key_capability_contract.py"
