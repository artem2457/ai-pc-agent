WinPE USB
=========

Нужен Windows ADK + WinPE add-on.

1. С сайта скачайте usb-agent-TOKEN.zip
2. Положите папку AIAgent на флешку (рядом с загрузочными файлами WinPE)
3. Либо соберите ISO:  powershell -File winpe\build-winpe.ps1
4. Запишите ISO на USB (Rufus)
5. На пустом ПК: Boot from USB
6. Агент сам подключится к серверу

После установки Windows агент снова стартует через FirstLogonCommands в Autounattend.xml
