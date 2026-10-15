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
2. **The target Ninox app** (module). Recommend a new, empty one, for example named after the custom app. One Ninox app holds one custom app, and each deploy sets who can read and create in the declared tables (Stage 4).
3. **The subdomain.** It becomes `https://<subdomain>.ninoxapps.com`, is unique across all Ninox customers, and **can never be changed** after the first deploy. Rules: 3 to 40 characters, lowercase letters, digits, and single hyphens, not starting or ending with a hyphen. Generic words such as `app`, `admin`, `api`, `www`, or `test` are reserved, and short names are often taken; suggest one with the company or project in it (`acme-rentals`, not `rentals`). `check_app.py --subdomain` checks the format and the reserved list.
4. **The folder** passed `check_app.py` with 0 errors, and they have it on their computer, unzipped into its own folder.
5. **Public data.** Everything the custom app can read is visible to anyone with the URL, and anyone with the URL can submit to its forms, as often as they like. Forms that collect personal data show the operator's privacy notice. For tests, use made-up data.

## Stage 2: Deploy

1. In Ninox, open the target Ninox app and go to its **Settings** (the address then ends in `/settings`).
2. Scroll to the **Custom app** section and click **Deploy custom app**.
3. **Subdomain:** enter the agreed subdomain.
4. **App folder:** click **Choose folder** and select **the packaged folder itself**: the folder that directly contains `app.json` and `dist/`. Not `dist/`, not a parent folder, not the zip file. From a zip: extract it into a new, empty folder and select that folder.
5. The browser asks to confirm uploading the folder's files to the site. Confirm.
6. Click **Deploy custom app**. A "Deployment in progress" toast appears, then "Deployment completed". Under the section: "Live release: …".
7. "Your app is being published. This can take a minute." Wait until the **Open custom app** button works.

On the first deploy, Ninox creates the tables and fields from `app.json` and imports the seed CSVs into the Ninox app. They appear in the Ninox app like any other table.

## Stage 3: Verify

1. Click **Open custom app**. The custom app opens at `https://<subdomain>.ninoxapps.com`.
2. Check that the data shows. Rows the row filter excludes (for example unpublished ones) must not show.
3. Submit one test entry with made-up data.
4. In the Ninox app, open the table the form writes to and find the new row. Attached files are in that row's documents.

If the start page shows a plain `Not found`, wait a minute and reload; the address takes a moment to go live after the first deploy.

## Stage 4: Tidy up in Ninox

- **Field types.** Tables without a seed CSV have only Text fields. Change numbers, dates, and amounts to the right type in the Ninox app's data model; later deploys never change field types again. After that, the form must send values of the new type, or visitors get `Internal server error` on submit.
- **Labels.** Rename table and field labels freely; the custom app uses the internal names, which stay the same.
- **Permissions.** Each deploy sets who may read and create in the declared tables. With the usual viewer mapping, workspace editors can no longer read the app's read tables or add rows to its create tables, and workspace viewers can add rows to its create tables. Admins keep full access. `check_app.py` prints these effects per table. Plan team access around it, and expect manual changes to these permissions to be reset on the next deploy.

## Updating the custom app

Edit the source folder, test it with `mock_gateway.py`, package it again with `check_app.py --package … --overwrite`, then in the same Settings section click **Update custom app** and choose the packaged folder. The subdomain is fixed.

- Files in `dist/` are replaced completely by the new upload.
- New tables and fields in `app.json` are created (as Text, unless a new table has a seed CSV).
- Seed CSVs for existing tables are **merged by their first column**: matching rows are updated, new keys added, nothing deleted. Changes made in Ninox to seeded columns are overwritten by the CSV values, so after go-live, maintain catalog data in Ninox and drop the CSV (or keep it identical).
- Visitors who have the custom app open keep the old page until they reload; their session restarts by itself.

If a deploy fails, the previous version of the app keeps being served. Changes the deploy made to the Ninox app before it failed stay: new tables and fields, the permission changes, and imported CSV rows. Fix the cause and deploy again; the repeat does not duplicate them. There is no rollback or unpublish button; to take a custom app offline, ask Ninox support.

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
| A new, empty table appeared next to the existing one | Name in `app.json` differs from the existing table's internal name, so the deploy created a new table | Use the internal name, deploy again, delete the empty table in Ninox |
| Role … does not exist in this workspace. | Role name instead of role ID | Use `000000000000000000000003` |
| The release holds no index.html | `index.html` not at the `dist/` root | Move it to `dist/index.html` |
| This file looks like it carries a credential… | `.env`, `.npmrc`, `*.pem`, key files in `dist/` | Remove them from `dist/` |
| Images or fonts broken, app otherwise works | Binary files in `dist/` are corrupted by the upload | Use SVG, CSS, emoji, or external `https://` images |
| Page loads, data does not ("Couldn't load…") | 403 from the gateway; see the app's error text | Match it in `app-sdk.md`, "Errors and limits" |
| A `>` or `<` row filter shows the wrong rows | The field is Text, so `"100" < "20"` | Retype the field to Number in Ninox |
| `Internal server error` when submitting | A value does not fit a retyped field, an unknown choice option, a required field missing, or the record quota is reached | Send values of the field's type; check the field settings in Ninox |
| `This session did not create that row` when uploading | The session that created the row ended: 15 minutes after it started, or at a redeploy | Upload right after creating the row |
| The start page works, but a link to `/some/path` shows an error page | Path routing without a matching file; the host never falls back to `index.html` | Use hash routes (`#/some/path`) |
