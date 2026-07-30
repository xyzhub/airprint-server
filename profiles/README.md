# Built-in profiles

The canonical built-in profile files are packaged under
`src/airprint_server/data/profiles/` so installed wheels can always find them.
Add site-specific profiles to `/etc/airprint-server/profiles.d/`; those files
override a built-in profile with the same `id` and are never overwritten by
installation or package upgrades.

