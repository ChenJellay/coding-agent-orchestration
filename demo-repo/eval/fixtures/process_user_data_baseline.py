# Eval S2 baseline — reset copy for eval_fixtures/process_user_data.py


def process_user_data(d: dict) -> str:
    return d["email"].strip()
