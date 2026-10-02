/*
  Bit depth (BD) and total depth (TD) under each well's name.

  Both come from GET /wells, which the dashboard already polls, so the line
  follows the rig without a request of its own. The agent sends null for
  both while it cannot reach the rig, and that is drawn as "—" rather than
  leaving the last depth up looking live.

  app.js calls showDepths() when a card is built and on every poll.
*/

function formatDepth(value, unit) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return '—';
    }

    return Number(value).toFixed(2) + (unit ? ' ' + unit : '');
}


function showDepths(card, well) {
    const unit = well.depth_unit || '';

    card.querySelector('.well-bd').textContent = formatDepth(well.bit_depth, unit);
    card.querySelector('.well-td').textContent = formatDepth(well.total_depth, unit);
}
