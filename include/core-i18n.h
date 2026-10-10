#ifndef CORE_I18N_H
#define CORE_I18N_H

#ifdef __cplusplus
extern "C" {
#endif

/* Translate msgid using core-i18n.mo for the current locale.
 * Binds the text domain automatically on first use (no init call needed).
 * Returns msgid itself if no translation exists. */
const char *core_i18n_gettext(const char *msgid);

/* Optional: override the directory containing <lang>/LC_MESSAGES/core-i18n.mo.
 * Also respects the CORE_I18N_LOCALEDIR environment variable. */
void core_i18n_set_localedir(const char *dir);

#ifdef __cplusplus
}
#endif

#define CORE_I18N_(s) core_i18n_gettext(s)

#endif
