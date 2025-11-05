We have a bit of an issue with the reminder sync (potentially this will also impact other sync).

In Apple Reminders, in the "Reminders" list, there is a reminder as follows:

Title: Call mum
UUID: 19FF7739-542D-4852-94BB-F8ECCA89230C
Completed: True
Due Date: 2025-11-03 09:00:00
Creation Date: 2025-04-22 15:38:05 +0000
Modification Date: 2025-11-03 13:58:04 +0000
Has Recurrence: False

Let's refer to that as "The Apple One".

In our database, this is mapped to a corresponding task in CalDAV, which is as follows:

Summary: Call mum
UID: 19FF7739-542D-4852-94BB-F8ECCA89230C
Completed: True
Due Date: 2025-11-03 09:00:00
Last Modified: 2025-11-05 08:26:10+00:00
Has Recurrence: False
CalDAV URL: https://nc.vassallo.cloud/remote.php/dav/calendars/keith/tasks/19FF7739-542D-4852-94BB-F8ECCA89230C.ics

Let's refer to that as "The CalDAV One".

--

The Apple One was originally created in Apple Reminders. It was created in April 2025, and is a was one instance of a recurring reminder. It was last modified in Apple Reminders on 2025-11-03 13:58:04, when it was marked as completed.

On 2025-11-05 08:26:10+00:00, our sync process ran, and synced the Apple One to the CalDAV One. This updated the CalDAV One's "Last Modified" date to 2025-11-05 08:26:10+00:00.

--

Do you see the problem here? The last modified date of the CalDAV One is now later than the last modified date of the Apple One. This means that if we were to sync again, we would see that the CalDAV One is "newer" than the Apple One, and we would not propagate any changes from Apple Reminders to CalDAV. Moreover, the operation itself is futile. The Apple One and the CalDAV One are now identical, so there is no need to sync them again. However, because of the last modified date discrepancy, our sync logic will not be able to determine that they are identical, and will not be able to correctly handle future updates.

--

To fix this, we need to ensure that when we sync from Apple Reminders to CalDAV, we do not update the "Last Modified" date of the CalDAV One to a date later than the "Modification Date" of the Apple One. Instead, we should set the "Last Modified" date of the CalDAV One to match the "Modification Date" of the Apple One when syncing.
