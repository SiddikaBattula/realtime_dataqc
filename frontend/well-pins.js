/*
  Pinning well cards.

  Each card pins and unpins on its own, and any number can be pinned. A pinned
  card stays exactly where it is in the grid - it is never moved - and is
  marked instead: a coloured header and a glowing border (well-pins.css), and a
  pulse when it is pinned so the eye goes to it.

  It used to move pinned cards to the front. That made pinning the second card
  light up the first slot, which read as the wrong well having been pinned.

  Pins are a personal view, not a setting of the well: they are kept in this
  browser's localStorage, as a list of well names under the signed-in person's
  email, so one person's pins change nobody else's dashboard.

  app.js calls initPin() for every card it builds. Uses auth from app.js.
*/

function pinStorageKey() {
    const who = (auth.user && auth.user.email) || 'anonymous';

    return 'dataqc.pins.' + who;
}


function loadPins() {
    try {
        const saved = JSON.parse(localStorage.getItem(pinStorageKey()));

        return Array.isArray(saved) ? saved.filter((name) => typeof name === 'string') : [];
    } catch (err) {
        // Private window or blocked storage: pins just will not persist.
        return [];
    }
}


function savePins(pins) {
    try {
        localStorage.setItem(pinStorageKey(), JSON.stringify(pins));
    } catch (err) {
        // Not worth bothering anyone about.
    }
}


function applyPin(card, pinned) {
    const button = card.querySelector('.well-pin');

    card.toggleAttribute('data-pinned', pinned);
    button.setAttribute('aria-pressed', String(pinned));
    button.title = pinned ? 'Unpin this well' : 'Pin this well';
}


function focusPinned(card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // Restart the pulse even if it ran a moment ago.
    card.classList.remove('well-pin-pulse');
    void card.offsetWidth;
    card.classList.add('well-pin-pulse');
}


/* Pin or unpin one well, touching no other. Returns whether it is now pinned. */
function togglePin(name) {
    const pins = loadPins();
    const at = pins.indexOf(name);

    if (at >= 0) {
        pins.splice(at, 1);
    } else {
        pins.push(name);
    }

    savePins(pins);

    return at < 0;
}


function initPin(card, name) {
    applyPin(card, loadPins().includes(name));

    card.querySelector('.well-pin').addEventListener('click', () => {
        const pinned = togglePin(name);

        applyPin(card, pinned);

        if (pinned) {
            focusPinned(card);
        }
    });

    card.addEventListener('animationend', (event) => {
        if (event.animationName === 'well-pin-pulse') {
            card.classList.remove('well-pin-pulse');
        }
    });
}
