# iCloudBridge User Guide

[< Back to Table of Contents](user.md)

## The Passwords Page
The Passwords page in the iCloudBridge WebUI allows you to manage the synchronisation of passwords stored in Apple Passwords with your chosen service. From this page, you can perform a synchronisation or simulate a sync to see what would change. 

### How it Works & Limitations
Unlike Notes, Reminders and Photos, Passwords does not support automatic synchronisation. This means that you need to carry out the sync manually. iCloudBridge tries to make this process as simple as possible. Here's how it works:

1. Export your passwords to a CSV file from Apple Passwords. 
2. Import your passwords export into iCloudBridge. 
3. iCloudBridge sends any new/updated passwords to your remote location (i.e. Bitwarden, Vaultwarden or Nextcloud Passwords). 
4. Any new passwords found remotely are imported, and you are given a CSV file to import into Apple Passwords. 

> [!NOTE]
> One-Time Passwords (also known as TOTP, used for two-factor authentication) are supported, but only if you're using Bitwarden or Vaultwarden

> [!NOTE]
> Passkeys are **not** synchronised. There is no current mechanism for extracting these from Apple Passwords. 

### Syncing Passwords (Bidirectional)
To start a sync, you'll need to export your passwords from Apple Passwords. From Apple Passwords, click File > Export All Passwords to File...

![Exporting Apple Passords](images/docs_passwords_1.png)

This creates a CSV file in the folder you choose. 

Next, from iCloudBridge, click "Upload Apple CSV" and choose the CSV file you just exported from Apple Passwords. 

![Password Upoading CSV](images/docs_passwords_2.png)

At this point, you should probably run a simulation (especially if this is your first sync) as a sanity check. Click "Simulate", and observe the results:

![Password Simulation Results](images/docs_passwords_3.png)

Here we see that if we had to run a sync, we'd get 1 new password in Apple Passwords, and another in Vaultwarden (this would look the same for Bitwarden or Nextcloud Passwords). 

You can expand the result to see which passwords would actually be imported:

![Password Simulation Results Detail](images/docs_passwords_4.png)

So here, we'd get a new password titled "New Password from Vaultwarden" in Apple Passwords, and a new password titled "New Password from Apple Passwords" in Vaultwarden. 

Once you've confirmed everything looks good, you can proceed to an actual sync, by clicking the "Sync" button. You'll see results similar to a simulation, except this time the sync actually added passwords to Bitwarden/Vaultwarden or Nextcloud Passwords, and a file has been prepared for import into Apple Passwords.

![Password Sync Results](images/docs_passwords_5.png)

If passwords need to be imported into Apple Passwords, you'll see a button to download a CSV file for import. 

> [!IMPORTANT]
> For your security, the download link expires after 5 minutes, so make sure you download it!

Importing this file into Apple Passwords is easy. Simply click File > Import Passwords from File... and choose the CSV you just downloaded from iCloudBridge.

![Passwords import to Apple Passwords](images/docs_passwords_6.png)

Your new password will now be visible in Apple Passwords. 

![Passwords new in Apple Passwords](images/docs_passwords_7.png)

> [!WARNING]
> After importing, Apple Passwords will ask whether you want to delete the import file. Go ahead and do this - as this file contains plain-text passwords!

You can also check Bitwarden/Vaultwarden or Nextcloud passwords to confirm that your new passwords were imported. 

![Passwords new in Vaultwarden](images/docs_passwords_8.png)

### Unidirectional Sync

Besides the bidirectional sync, you can also do an Export (i.e. Apple Passwords to another service) or an Import (Another service to Apple Passwords). 

![Passwords Unidirectional Sync](images/docs_passwords_9.png)

---

[< Previous - Reminder Synchronisation](reminders.md) | [Next - Photo Synchronisation >](photos.md)