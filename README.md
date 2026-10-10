# i18ncore

The Standard Dictionary for Modern Applications

Stop translating "Cancel", "Submit", and "Privacy Policy" over and over again. i18ncore is a zero-dependency, context-aware translation library designed to provide high-quality, pre-translated strings for common UI elements. Covering over 50+ languages, it bridges the gap between raw CLDR data and application-level business logic, allowing developers to localize their apps in seconds rather than weeks.

## C/C++ library

```sh
cmake -B build && cmake --build build && sudo cmake --install build   # needs msgfmt
cc app.c $(pkg-config --cflags --libs core-i18n)
```

```c
#include <core-i18n.h>
setlocale(LC_ALL, "");
puts(core_i18n_gettext("Cancel"));
```

`core-i18n.mo` is located and loaded automatically from the install locale dir (override with `CORE_I18N_LOCALEDIR`).

## Qt Quick / QML

If Qt Qml (Qt 5 or Qt 6) is available when configuring the project, CMake also builds and installs an optional QML plugin. Import `CoreI18n` in a QML file to call `core_i18n()` as a global function:

```qml
import QtQuick
import CoreI18n 1.0

Text {
    text: core_i18n("Cancel")
}
```

The function uses the same current locale and gettext catalogs as the C API. When running from the build tree, add `build/qml` to the QML import path.
