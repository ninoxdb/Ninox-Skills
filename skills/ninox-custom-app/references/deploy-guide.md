# Deploy guide

Walk the user through these stages in their language. Give one stage at a time, and wait for the user to confirm or send a screenshot before moving on. In the Ninox 4 interface a module is called an **app**; this guide says "Ninox app" for the module and "custom app" for the web app being deployed.

UI labels in English and German:

| English | German |
|---|---|
| Custom app | Benutzerdefinierte App |
| Deploy custom app | Benutzerdefinierte App bereitstellen |
| Update custom app | Benutzerdefinierte App aktualisieren |
| Subdomain | Subdomain |
| App folder | App-Ordner |
| Choose folder | Ordner auswählen |
| Open custom app | Benutzerdefinierte App öffnen |
| Deployment in progress | Bereitstellung läuft |
| Deployment completed | Bereitstellung abgeschlossen |

## Stage 1: Before deploying

Confirm with the user:

1. **They are a workspace admin.** The "Custom app" section only appears for admins, and only when Custom Apps is enabled for the workspace. If it is missing for an admin, Custom Apps is not enabled yet; they should contact Ninox support.
2. **The target Ninox app** (module). Recommend a new, empty one, for example named after the custom app. One Ninox app holds one custom app, and each deploy resets who can read and create in the declared tables.
3. **The subdomain.** It becomes `https://<subdomain>.ninoxapps.com`, is unique across all Ninox customers, and **can never be changed** after the first deploy. Rules: 3 to 40 characters, lowercase letters, digits, and single hyphens, not starting or ending with a hyphen. Generic words such as `app`, `admin`, `api`, `www`, or `test` are reserved, and short names are often taken; suggest one with the company or project in it (`acme-rentals`, not `rentals`). `check_app.py --subdomain` checks the format and the reserved list.
4. **The folder** passed `check_app.py` with 0 errors, and they have it on their computer, unzipped.
5. **Public data.** Everything the custom app can read is visible to anyone with the URL. Inbox tables collect what visitors type; for tests, use made-up data.

## Stage 2: Deploy

1. In Ninox, open the target Ninox app and go to its **Settings** (the address then ends in `/settings`).
2. Scroll to the **Custom app** section and click **Deploy custom app**.
3. **Subdomain:** enter the agreed subdomain.
4. **App folder:** click **Choose folder** and select **the app folder itself**: the folder that directly contains `app.json` and `dist/`. Not `dist/`, not a parent folder, not the zip file.
5. The browser asks to confirm uploading the folder's files to the site. Confirm.
6. Click **Deploy custom app**. A "Deployment in progress" toast appears, then "Deployment completed". Under the section: "Live release: …".
7. "Your app is being published. This can take a minute." Wait until the **Open custom app** button works.

On the first deploy, Ninox creates the tables and fields from `app.json` and imports the seed CSVs into the Ninox app. They appear in the Ninox app like any other table.

## Stage 3: Verify

1. Click **Open custom app**. The custom app opens at `https://<subdomain>.ninoxapps.com`.
2. Check that catalog data shows. Unpublished rows (row filter) must not show.
3. Submit one test entry with made-up data.
4. In the Ninox app, open the inbox table and find the new row. Attached files are in that row's documents.

If the page says `No app is served at this address`, wait a minute and reload; the address takes a moment to go live after the first deploy.

## Stage 4: Tidy up in Ninox

- **Field types.** Inbox tables (no seed CSV) have only Text fields. Change numbers, dates, and amounts to the right type in the Ninox app's data model. Later deploys never change field types again.
- **Labels.** Rename table and field labels freely; the custom app uses the internal names, which stay the same.
- **Permissions.** Each deploy sets who may read and create in the declared tables, which can hide those tables from non-admin workspace members. Give the team access through a separate Ninox app or view if they need it.

## Updating the custom app

Edit the folder, rerun `check_app.py`, then in the same Settings section click **Update custom app** and choose the folder again. The subdomain is fixed.

- Files in `dist/` are replaced completely by the new upload.
- New tables and fields in `app.json` are created (as Text, unless a new table has a seed CSV).
- Seed CSVs for existing tables are **merged by their first column**: matching rows are updated, new keys added, nothing deleted. Changes made in Ninox to seeded columns are overwritten by the CSV values, so after go-live, maintain catalog data in Ninox and drop the CSV (or keep it identical).
- Visitors who have the custom app open keep the old page until they reload; their session restarts by itself.

If a deploy fails, the previous version keeps running. There is no rollback or unpublish button; to take a custom app offline, ask Ninox support.

## Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| The folder has no app.json. Select a folder that contains it. | Selected `dist/` or a parent folder | Select the folder that directly contains `app.json` |
| The folder needs a dist folder with your built files, next to app.json. | No `dist/` next to `app.json` | Build into `dist/` |
| Use 3 to 40 lowercase letters, numbers, or single hyphens… | Subdomain format | Fix the subdomain |
| `handle_reserved` / The handle X is taken | Reserved word, or used by another customer | Pick a more specific subdomain |
| app.json is not valid | Manifest error; the details list each one | Run `check_app.py`, fix every error |
| app.json names a table or field that is not a valid internal name | Uppercase, hyphen, or camelCase name | Rename to lowercase `snake_case` |
| The CSV for X starts with "col", which is not a field of that table | Redeploy with a CSV whose first column is not a field | Make the first column a declared key field |
| The CSV for X repeats / has an empty / padded value in its first column | Key column not unique or not clean | Fix the CSV's first column |
| Table X does not exist in this module. | Name in `app.json` differs from the internal name in Ninox | Use the internal name |
| Role … does not exist in this workspace. | Role name instead of role ID | Use `000000000000000000000003` |
| The release holds no index.html | `index.html` not at the `dist/` root | Move it to `dist/index.html` |
| This file looks like it carries a credential… | `.env`, `.npmrc`, `*.pem`, key files in `dist/` | Remove them from `dist/` |
| Images or fonts broken, app otherwise works | Binary files in `dist/` are corrupted by the upload | Use SVG, CSS, emoji, or external `https://` images |
| Page loads, data does not ("Couldn't load…") | 403 from the gateway; see the app's error text | Match it in `app-sdk.md`, "Errors and limits" |
| Row filter shows nothing | Filter field is Text, not boolean or number | Change the field type in Ninox, or fix the CSV and recreate the table |
| Deep link shows an error page | Path routing without a matching file | Use hash routes |
