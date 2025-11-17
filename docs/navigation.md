# iCloudBridge User Guide

[< Back to Table of Contents](user.md)

## Using the WebUI

The iCloudBridge WebUI provides a user-friendly interface to manage your synchronisation settings, monitor sync status, and access logs. This guide will walk you through the main features of the WebUI.

### Dashboard
Upon logging into the WebUI, you'll be presented with the Dashboard. Here, you can see an overview of your synchronisation status, including the last sync time and any errors that may have occurred.

![Dashboard Overview](images/docs_nav_dashboard.png)

To change settings and configure synchronisation options, use one of the navigation links in the sidebar. Here's what these do:

- **Dashboard**: Returns you to the main dashboard view.

- [**Notes**](notes.md): Manage Apple Notes synchronisation. This lets you choose which folders to sync and how folders map between Apple Notes and your chosen service. You can also simulate a sync or trigger a manual sync from here.

- [**Reminders**](reminders.md): Manage Apple Reminders synchronisation. Similar to the Notes page, you can select which reminder lists to sync, adjust list/calendar mappings, simulate syncs or trigger a manual sync.

- [**Passwords**](passwords.md): Manage Apple Passwords synchronisation. Here, you can manually perform a bidirectional synchronisation, or you can export/import passwords to/from your chosen service.

- [**Photos**](photos.md): Manage Apple Photos synchronisation. This page allows you to view sync status, simulate syncs or trigger manual syncs.

- [**Schedules**](schedules.md): Configure automated synchronisation schedules. You can set up periodic syncs for each service, specifying the frequency and time of day for the syncs to occur.

- [**Logs**](logs.md): View detailed logs of synchronisation activities. This is useful for troubleshooting any issues that may arise during sync operations.

- **Settings**: Access and modify your synchronisation settings, including service configurations and folder mappings - these are the same settings you configured in the First-Run Wizard.

### Settings
The settings page shows you all of the configuration which was saved when you ran the first-time wizard. From here, you can enabled/disable services, as well as change any of the settings you provided during the wizard.

![Settings Page](images/docs_nav_settings.png)

The settings page also allows you to reset individual services. For example, resetting the Reminder sync service will forget your Nextcloud/CalDAV credentials and list/calendar mappings, allowing you to reconfigure them from scratch. Note that none of your data will be deleted when you reset a service; it simply clears the configuration for that service.

If you want to start fresh, click the "Complete Reset" button at the bottom of the settings page. This will clear all saved configurations for all services, allowing you to run the First-Run Wizard again from scratch. Again, none of your data will be deleted when performing a complete reset.

---

[> Next - Note Synchronisation](notes.md)

