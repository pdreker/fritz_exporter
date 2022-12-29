Helping out
===========

If you think, that there are more metrics which can be read from your device or you just want to help out by providing a snapshot of the data, which can be read from your device or devices the exporter provides a function to gather information from your devices and send it to the author of this project.

What it does
------------

The "data donation" function will collect the API functions which your device reports, the exporter capabilities detected for your device, the type of device and the current OS version. Additionally it will try reading all information, which the API provides. The information is sanitized before it is uploaded so there should not be any private data (e.g. usernames, passwords, IP addresses etc.) in the output.

It then uploads that information to the project. The (quite trivial) server side of this can be seen at `this GitHub Repo <https://github.com/pdreker/fritz_datacollector>`_.

You can verify the output before sending it to me, by printing it to the screen and check for private data and telling the exporter to sanitize any additional bits I may have missed in the code.

What it does not do
-------------------

This will try the hardest not to collect any private information from your device. While I cannot guarantee, that it will never contain any private data in the output, as this function blindly scans all "Get" endpoints your device offers, I have taken steps to ensure that it should already sanitize the most obvious "leaks".

What the data will be used for
------------------------------

The data collected will be used for testing the code against a wider variety of devices and can be used to identify more metrics which I may be unable to reverse engineer, simply because I do not have that device or type of internet connection.

Actually donating data
----------------------

The data donation can be done using the normal docker image, which is also used to run the exporter. Simply add the ``--donate-data`` option to the command line to show what data would be collected.

Assuming you have the config file in your current directory, simply run

.. code-block:: bash

    docker run -v $(pwd)/fritz-exporter.yml:/app/fritz-exporter.yml --rm pdreker/fritz_exporter:latest --config fritz-exporter.yml --donate-data

Or if you use environment variables for the configuration:

.. code-block:: bash

    docker run -e FRITZ_HOSTNAME="fritz.box" -e FRITZ_USERNAME="myusername" -e FRITZ_PASSWORD="mypassword" --rm pdreker/fritz_exporter:latest --donate-data

After you check the data printed to screen you can simply add the ``--upload-data`` parameter to the end of the command line and instead of printing out the data to screen it will be uploaded:

.. code-block:: bash

    docker run -e FRITZ_HOSTNAME="fritz.box" -e FRITZ_USERNAME="myusername" -e FRITZ_PASSWORD="mypassword" --rm pdreker/fritz_exporter:latest --donate-data --upload-data

After the data is uploaded successfully you will see a log message with an ID. If you want to open an issue you can use the ID to reference your data, so I can find it.

Help! There is actually private data in my output!
--------------------------------------------------

If there is any data you do not want to share in the output there is an option to sanitize the output further. By adding the ``-s SERVICE ACTION FIELD`` option you can tell the donation to sanitize the corresponding field. If you omit the ``FIELD`` all fields for that action will be sanitized. Please use this sparingly, as the more data is present, the more useful the data is.

For example you have the following output snippet

.. code-block:: text

    ...
            }
      },
      "WANIPConnection1": {
        "GetInfo": {
          "NewEnable": "True",
          "NewConnectionStatus": "Connecting",
          "NewPossibleConnectionTypes": "IP_Routed, IP_Bridged",
          "NewConnectionType": "IP_Routed",
          "NewName": "mstv",
          "NewUptime": "0",
    ...

and you want the ``NewName`` field to be sanitized, you can add ``-s WANIPConnection1 GetInfo NewName`` to your command line and your output will now look like this:

.. code-block:: text

    ...
            }
      },
      "WANIPConnection1": {
        "GetInfo": {
          "NewEnable": "True",
          "NewConnectionStatus": "Connecting",
          "NewPossibleConnectionTypes": "IP_Routed, IP_Bridged",
          "NewConnectionType": "IP_Routed",
          "NewName": <SANITIZED>,
          "NewUptime": "0",
    ...

If you just specified ``-s WANIPConnection1 GetInfo`` all fields in the ``GetInfo`` block would be sanitized. The ``-s`` (or ``--sanitized``) option can be repeated multiple times, as needed:

.. code-block:: bash

    docker run -e FRITZ_HOSTNAME="fritz.box" -e FRITZ_USERNAME="myusername" -e FRITZ_PASSWORD="mypassword" --rm pdreker/fritz_exporter:latest --donate-data -s WANIPConnection1 GetInfo NewName -s WANPPPConnection1 GetInfo NewRSIPAvailable
