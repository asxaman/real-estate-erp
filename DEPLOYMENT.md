# Going live checklist

Do these in order. Tick each one.

## 1. In the system
- [ ] System settings -> Development tab: untick *Development mode* and save (this also switches off the demo accounts).
- [ ] Users page: check there are no accounts you do not recognise; every person has their own login.
- [ ] Roles page: check who has the *export* permissions.

## 2. Settings on the server (environment variables or the `.env` file)
- [ ] `DJANGO_SECRET_KEY` = a long random text (32+ characters). The server will not start without it.
- [ ] `DJANGO_ALLOWED_HOSTS` = your website address, for example `erp.mycompany.ae`.
- [ ] `DJANGO_CSRF_TRUSTED_ORIGINS` = `https://erp.mycompany.ae`
- [ ] `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` for the live PostgreSQL database.
- [ ] Do NOT set `DJANGO_DEBUG=1` on the server.
- [ ] Behind nginx or a load balancer: `DJANGO_BEHIND_PROXY=1`. Optional: `DJANGO_SESSION_MINUTES` (default 60).

## 3. Web server
- [ ] The site is served over HTTPS (a certificate is installed). The settings force HTTPS and secure cookies.
- [ ] Static files: run `python manage.py collectstatic` and serve the `staticfiles` folder at `/static/`.
- [ ] Do NOT serve the `media` folder directly. All downloads go through the application, which checks permission.
  With nginx you may let nginx send the file after the check: make an `internal` location for the media folder
  and set `DJANGO_MEDIA_ACCEL` to its address (for example `/protected-media/`).
- [ ] Run the application with a proper server (gunicorn, waitress ...), not `runserver`.

## 4. Check
- [ ] `python manage.py check --deploy` shows no warnings.
- [ ] `python manage.py test` passes (on a test copy, never on the live database).
- [ ] Log in from another computer; open an uploaded document; try a wrong password 5 times to see the lock-out.

## 5. Backups
- [ ] Schedule `python manage.py backup_db` (database + uploaded files) at least daily.
- [ ] Copy the `backups` folder to another place (other disk or cloud).
- [ ] Practise one restore on a test machine.
