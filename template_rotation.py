"""
Smart Template Rotation — selects the best-performing template variant
based on historical response rates, with fallback to round-robin.
"""
import random
import logging
import database as db

logger = logging.getLogger("tg-scheduler.template-rotation")

# Minimum sends before a variant's response rate is considered reliable
MIN_SENDS_FOR_STATS = 5


async def select_variant(messages_list: list, template_id: int = None,
                          campaign_id: int = None, watcher_id: int = None) -> tuple[list, int]:
    """
    Select the best template variant from a list of message sets.

    Args:
        messages_list: List of message variants (each variant is a list of message dicts)
        template_id: The template ID if using template library
        campaign_id: Campaign ID for tracking
        watcher_id: Watcher ID for tracking

    Returns:
        (selected_messages, variant_index)
    """
    if not messages_list or len(messages_list) <= 1:
        return (messages_list[0] if messages_list else [], 0)

    if template_id:
        # Try to get performance data
        perf = await db.get_template_performance(template_id=template_id)

        # Filter variants with enough data
        reliable = [p for p in perf if p.get("total_sent", 0) >= MIN_SENDS_FOR_STATS]

        if reliable:
            # Weighted random selection based on response rate
            # Higher response rate = higher probability of selection
            # But still give other variants a chance (exploration)
            weights = []
            for i in range(len(messages_list)):
                variant_perf = next((p for p in reliable if p["variant_index"] == i), None)
                if variant_perf and variant_perf["response_rate"] > 0:
                    weights.append(variant_perf["response_rate"] + 0.1)  # Base weight
                else:
                    weights.append(0.2)  # Exploration weight for untested variants

            # Weighted random choice
            total = sum(weights)
            r = random.random() * total
            cumulative = 0
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    logger.debug(f"[TemplateRotation] Selected variant {i} (weighted, rate={weights[i]:.2f})")
                    return (messages_list[i], i)

    # Fallback: round-robin / random
    idx = random.randint(0, len(messages_list) - 1)
    logger.debug(f"[TemplateRotation] Selected variant {idx} (random fallback)")
    return (messages_list[idx], idx)


async def record_send(template_id: int, variant_index: int,
                       campaign_id: int = None, watcher_id: int = None):
    """Record a successful send for a template variant."""
    await db.update_template_performance(
        template_id, variant_index,
        campaign_id=campaign_id, watcher_id=watcher_id,
        sent_delta=1
    )


async def record_reply(template_id: int, variant_index: int,
                        campaign_id: int = None, watcher_id: int = None):
    """Record a reply for a template variant."""
    await db.update_template_performance(
        template_id, variant_index,
        campaign_id=campaign_id, watcher_id=watcher_id,
        reply_delta=1
    )
