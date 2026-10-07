.PHONY: test validate

test:
	python3 -m unittest discover -s tests

validate:
	python3 translate_project.py --validate-only
