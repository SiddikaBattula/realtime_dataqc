/*
  The Email recipients tab.

  One block per base region, and under it whoever should be told about that
  base - as many people as it has, under whatever role names suit:

      mumbai
        base_head          Siddika@ofiindia.com
        operational_head   someone.else@ofiindia.com

  The roles are not fixed. Adding a person to a region sends to them too, with
  nothing in any file to change, which is why this is a list under each region
  rather than two boxes labelled in the markup.

  A well names its base region beside its IP address; this says where that name
  sends mail. Two separate things on purpose - a coordinator changing does not
  mean re-saving every well on that base, and a well moving base does not mean
  editing this list.

  Its own file rather than more of app.js, which is already the grid, the
  cards, the resizing and the rule form. Nothing in here touches any of those:
  it owns one tab, one endpoint and the list of region names that the well
  form's datalist offers.

  A plain script like shared.js, loaded before app.js, so the names below are
  visible to it.
*/

'use strict';

const emailEl = {
    form: document.getElementById('email-form'),
    intro: document.getElementById('email-intro'),
    rows: document.getElementById('email-rows'),
    add: document.getElementById('email-add'),
    save: document.getElementById('email-save'),
    error: document.getElementById('email-error'),
    regions: document.getElementById('known-regions'),
};

/*
  The regions being edited, as [{ region, people: [{ role, email }] }].

  A list of pairs rather than the {role: email} object the file holds, because
  an object cannot be edited in place: renaming a role would mean deleting one
  key and adding another on every keystroke, and two people briefly sharing a
  role name would silently lose one of them. It is turned back into an object
  on save, which is where a duplicate role is caught.
*/
let emailRegions = [];

// How long a window each digest covers, from the server, so the tab says what
// it actually is rather than repeating a number someone changed in .env.
let intervalMinutes = 10;

function emailError(message) {
    emailEl.error.textContent = message || '';
    emailEl.error.hidden = !message;
}

/*
  The region names offered on the well form.

  A well is joined to its people by the region name being typed the same in
  both places. It is matched without regard to case, but not to spelling -
  "mumbai" against "mumbia" is a well whose alerts go to nobody, and nothing
  about the card would say so. Offering the names already saved is what keeps
  the two in step.
*/
function refreshRegionList() {
    emailEl.regions.replaceChildren();

    for (const entry of emailRegions) {
        if (!entry.region.trim()) {
            continue;
        }

        const option = document.createElement('option');
        option.value = entry.region;
        emailEl.regions.append(option);
    }
}

function emailInput(placeholder, value, type, onInput) {
    const input = document.createElement('input');

    input.type = type;
    input.value = value || '';
    input.placeholder = placeholder;
    input.spellcheck = false;

    input.addEventListener('input', () => {
        onInput(input.value);
        emailError('');
    });

    return input;
}

function removeButton(title, onClick) {
    const button = document.createElement('button');

    button.type = 'button';
    button.className = 'icon-btn email-remove';
    button.title = title;
    button.textContent = '×';
    button.addEventListener('click', onClick);

    return button;
}

function renderPeople(host, entry) {
    host.replaceChildren();

    const head = document.createElement('div');
    head.className = 'email-person email-head';

    for (const label of ['Role', 'Email address', '']) {
        const cell = document.createElement('span');
        cell.textContent = label;
        head.append(cell);
    }

    host.append(head);

    entry.people.forEach((person, index) => {
        const line = document.createElement('div');
        line.className = 'email-person';

        line.append(
            emailInput('base_head', person.role, 'text', (v) => {
                person.role = v;
            }),
            emailInput('someone@ofiindia.com', person.email, 'email', (v) => {
                person.email = v;
            }),
            removeButton('Remove this person', () => {
                entry.people.splice(index, 1);
                renderPeople(host, entry);
                emailError('');
            }),
        );

        host.append(line);
    });

    const add = document.createElement('button');

    add.type = 'button';
    add.className = 'btn btn-small';
    add.textContent = 'Add a person';

    add.addEventListener('click', () => {
        entry.people.push({ role: '', email: '' });
        renderPeople(host, entry);

        const inputs = host.querySelectorAll('.email-person:not(.email-head) input');
        const roleBox = inputs[(entry.people.length - 1) * 2];

        if (roleBox) {
            roleBox.focus();
        }
    });

    host.append(add);
}

function renderEmailRegions() {
    emailEl.rows.replaceChildren();

    if (!emailRegions.length) {
        const empty = document.createElement('p');

        empty.className = 'email-empty';
        empty.textContent =
            'No regions yet. Add one, then enter its name on each well that '
            + 'belongs to it.';

        emailEl.rows.append(empty);
        return;
    }

    emailRegions.forEach((entry, index) => {
        const block = document.createElement('div');
        block.className = 'email-region';

        const head = document.createElement('div');
        head.className = 'email-region-head';

        const label = document.createElement('span');
        label.textContent = 'Base region';

        head.append(
            label,
            emailInput('mumbai', entry.region, 'text', (v) => {
                entry.region = v;
                refreshRegionList();
            }),
            removeButton('Remove this region', () => {
                emailRegions.splice(index, 1);
                renderEmailRegions();
                refreshRegionList();
                emailError('');
            }),
        );

        const people = document.createElement('div');
        people.className = 'email-people';

        renderPeople(people, entry);

        block.append(head, people);
        emailEl.rows.append(block);
    });
}

/*
  Turn the edited list back into what the file holds.

  A line left completely blank is dropped rather than refused - an "Add a
  person" clicked by mistake should not have to be found and removed before
  anything can be saved. A role with no address is kept, because that is
  someone not appointed yet and worth leaving in the file as a reminder; the
  server refuses only a region where nobody at all has an address.
*/
function asDocument() {
    const regions = {};

    for (const entry of emailRegions) {
        const people = {};

        for (const person of entry.people) {
            const role = person.role.trim();
            const email = person.email.trim();

            if (!role && !email) {
                continue;
            }

            people[role] = email;
        }

        regions[entry.region.trim()] = people;
    }

    return regions;
}

/*
  Load the regions, and say whether anything can actually be sent.

  `configured` is false until EMAIL_ENABLED, SMTP_HOST and SMTP_FROM are all
  set in .env. Saying so here is the difference between "nobody is getting
  these" and a tab that looks finished because the addresses are filled in.
*/
async function loadEmailRecipients() {
    emailError('');

    try {
        const data = await api('/email/config');
        const regions = data.regions || {};

        emailRegions = Object.keys(regions).map((region) => ({
            region,
            people: Object.entries(regions[region]).map(([role, email]) => ({
                role,
                email,
            })),
        }));

        intervalMinutes = data.interval_minutes || intervalMinutes;

        emailEl.intro.textContent = data.configured
            ? 'Every ' + intervalMinutes + ' minutes, the alerts raised in that '
              + 'time are emailed to everyone listed under that well’s base '
              + 'region. A window with no alerts sends nothing.'
            : 'No mail server is set up, so nothing is being sent. Set '
              + 'EMAIL_ENABLED, SMTP_HOST and SMTP_FROM in .env and restart. '
              + 'The regions below can still be filled in now.';

        emailEl.intro.dataset.tone = data.configured ? '' : 'warn';

        renderEmailRegions();
        refreshRegionList();
    } catch (err) {
        emailError('Could not load the regions: ' + err.message);
    }
}

emailEl.add.addEventListener('click', () => {
    emailRegions.push({
        region: '',
        people: [{ role: 'base_head', email: '' }],
    });

    renderEmailRegions();

    const boxes = emailEl.rows.querySelectorAll('.email-region-head input');
    const last = boxes[boxes.length - 1];

    if (last) {
        last.focus();
    }
});

emailEl.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    emailError('');

    emailEl.save.disabled = true;
    emailEl.save.textContent = 'Saving…';

    try {
        await api('/email/config', {
            method: 'PUT',
            body: JSON.stringify(asDocument()),
        });

        toast('Recipients saved — used by the next pass');
        refreshRegionList();
    } catch (err) {
        // The API names the region and what is wrong with it, so it belongs
        // against the form and not in a toast that disappears.
        emailError(err.message);
    } finally {
        emailEl.save.disabled = false;
        emailEl.save.textContent = 'Save recipients';
    }
});
