#include "core-i18n.h"

#include <libintl.h>
#include <locale.h>
#include <stdlib.h>

#define DOMAIN "core-i18n"

static int bound;

static void bind_domain(const char *dir)
{
    bindtextdomain(DOMAIN, dir);
    bind_textdomain_codeset(DOMAIN, "UTF-8");
    bound = 1;
}

void core_i18n_set_localedir(const char *dir)
{
    bind_domain(dir);
}

const char *core_i18n_gettext(const char *msgid)
{
    if (!bound) {
        const char *env = getenv("CORE_I18N_LOCALEDIR");
        bind_domain(env && *env ? env : CORE_I18N_LOCALEDIR);
    }
    return dgettext(DOMAIN, msgid);
}
