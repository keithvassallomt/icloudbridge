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

![Exporting Apple Passwords](images/docs_passwords_1.png)

This creates a CSV file in the folder you choose. 

Next, from iCloudBridge, click "Upload Apple CSV" and choose the CSV file you just exported from Apple Passwords. 

![Password Uploading CSV](images/docs_passwords_2.png)

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

### Verification codes from Ente Auth

If you keep your verification codes in Ente Auth, iCloudBridge can work out which Apple Passwords login each one belongs to. Apple Passwords doesn't let apps add a verification code to a login, so you still add each code yourself, but you get a checklist with the setup key for every login.

Open **Verification codes from Ente Auth** at the bottom of the Passwords page, then:

1. In Ente Auth, open Settings > Data > Export codes and choose Plain text.
2. In Apple Passwords, choose File > Export All Passwords to File.
3. Upload both files and choose Preview.
4. For each matched login, open it in Apple Passwords, choose Edit > Set Up Verification Code > Enter Setup Key, and paste the setup key. You can scan the QR code instead.

> [!WARNING]
> When you're done, delete both export files. Between them they hold every password and verification code in plain text. iCloudBridge doesn't keep a copy of either.

Check any login marked with a warning before you add its code. It was matched on the service name alone, and Ente has the code for a different account name, so it may belong to another account.

Some codes use settings other than 6 digits every 30 seconds, such as 8 digits. These only show a QR code, because a setup key on its own doesn't carry those settings and would give the wrong codes.

An encrypted Ente export can't be read. Decrypt it first with `ente auth decrypt <export_file> <output_file>`, then upload the plain-text file.

Trashed codes, HOTP codes and Steam codes are listed as skipped. A login that already has a different verification code is listed and left unchanged. When more than one login could fit a code, nothing is chosen for you.

---

[< Previous - Reminder Synchronisation](reminders.md) | [Next - Photo Synchronisation >](photos.md)
