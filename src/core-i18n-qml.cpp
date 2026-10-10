#include "core-i18n.h"

#include <QByteArray>
#include <QJSValue>
#include <QJSEngine>
#include <QObject>
#include <QQmlExtensionPlugin>
#include <QQmlEngine>
#include <QString>

class CoreI18nQmlBridge : public QObject
{
    Q_OBJECT

public:
    using QObject::QObject;

    Q_INVOKABLE QString core_i18n(const QString &msgid) const
    {
        const QByteArray utf8 = msgid.toUtf8();
        return QString::fromUtf8(core_i18n_gettext(utf8.constData()));
    }
};

class CoreI18nQmlPlugin : public QQmlExtensionPlugin
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)

public:
    void registerTypes(const char *) override
    {
    }

    void initializeEngine(QQmlEngine *engine, const char *uri) override
    {
        QQmlExtensionPlugin::initializeEngine(engine, uri);

        auto *bridge = new CoreI18nQmlBridge(engine);
        const QJSValue makeFunction = engine->evaluate(
            "(function(bridge) { return function(msgid) { return bridge.core_i18n(msgid); }; })");
        const QJSValue function = makeFunction.call({engine->newQObject(bridge)});
        engine->globalObject().setProperty(QStringLiteral("core_i18n"), function);
    }
};

#include "core-i18n-qml.moc"
