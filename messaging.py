import os


def send_messages(messages: list[dict]) -> list[dict]:
    mode = os.getenv("WHATSAPP_MODE", "dry_run").lower()
    if mode != "dry_run":
        raise RuntimeError(
            "WHATSAPP_MODE is not supported yet. Use dry_run until a provider is configured."
        )

    return [
        {
            "phone": message["phone"],
            "provider_id": f"dry_run_{index}",
            "status": "DRY_RUN",
        }
        for index, message in enumerate(messages, start=1)
    ]
