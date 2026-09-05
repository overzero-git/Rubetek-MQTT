# Rubetek-MQTT
Интеграция устройств сбора и передачи данных (УСПД) Rubetek в Home Assistant

Данная интеграция позволяет использовать УСПД от компании Rubetek как источник данных с приборов учета.
ВАЖНО! Следующие системные требования нужны для корректной работы интеграции:
1. УСПД настраиваются с помощью фирменного ПО "Rubetek Инженер" - подробнее здесь https://support.rubetek.com/ru/fire-alarm/firmwares/
2. Можно использовать сторонний MQTT брокер либо установить интеграцию MQTT со встроенным брокером в Home Assistant. В данном случае ч настройках УСПД через ПО "Rubetek Инженер" следует указывать IP-адрес вашего сервера Home Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=Rubetek-MQTT&owner=https%3A%2F%2Fgithub.com%2Foverzero-git)
