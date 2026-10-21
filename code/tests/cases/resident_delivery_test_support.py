"""Drive the coordinator publication step explicitly in synchronous domain tests."""
from management_portal.society_service import resident_delivery_work, resident_intake_domain


def published_delivery(receipt):
    if receipt.get("accepted"):
        resident_delivery_work.drain(receipt["resident_id"])
        return {**resident_intake_domain._resident_message_delivery_result(
            receipt["resident_id"], receipt["message_id"],
        ), **receipt}
    return receipt
