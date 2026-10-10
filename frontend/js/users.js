/*
  Adding a person to login.json, from the dashboard.

  Only a Base head has the add_user permission, so only they are shown the
  "Add person" button (app.js loadUser). The server checks the same thing on
  /auth/roles and /auth/register, so hiding the button is a convenience and not
  the lock - a crafted request from anyone else is still refused.

  Loaded after app.js: it uses api(), toast() and auth from there.
*/

const person = {
    modal: document.getElementById('person-modal'),
    form: document.getElementById('person-form'),
    email: document.getElementById('person-email'),
    role: document.getElementById('person-role'),
    region: document.getElementById('person-region'),
    password: document.getElementById('person-password'),
    error: document.getElementById('person-error'),
    submit: document.getElementById('person-submit'),
};


function personError(message) {
    person.error.textContent = message || '';
    person.error.hidden = !message;
}


async function loadRoles() {
    if (person.role.options.length) {
        return;
    }

    const body = await api('/auth/roles');

    for (const name of body.roles) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name.replaceAll('_', ' ');
        person.role.append(option);
    }
}


async function openPersonModal() {
    person.form.reset();
    personError('');

    // Most people a Base head adds are on their own base.
    person.region.value = (auth.user && auth.user.base_region) || '';

    person.modal.hidden = false;
    person.email.focus();

    try {
        await loadRoles();
    } catch (err) {
        personError(err.message);
    }
}


function closePersonModal() {
    person.modal.hidden = true;
}


person.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    personError('');
    person.submit.disabled = true;

    try {
        const body = await api('/auth/register', {
            method: 'POST',
            body: JSON.stringify({
                email: person.email.value,
                role: person.role.value,
                base_region: person.region.value,
                password: person.password.value,
            }),
        });

        toast(body.email + ' added — they can sign in now');
        closePersonModal();
    } catch (err) {
        // Kept on the form: it says which field to fix.
        personError(err.message);
    } finally {
        person.submit.disabled = false;
    }
});


document.getElementById('add-person-open').addEventListener('click', openPersonModal);
document.getElementById('person-close').addEventListener('click', closePersonModal);

person.modal.addEventListener('click', (event) => {
    if (event.target === person.modal) {
        closePersonModal();
    }
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !person.modal.hidden) {
        closePersonModal();
    }
});
