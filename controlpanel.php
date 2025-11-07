<!DOCTYPE html>
<html lang="en">
     
<head>
     <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Speed Display</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
        }
        .button {
            display: inline-block;
            padding: 10px 20px;
            font-size: 16px;
            cursor: pointer;
            margin: 10px;
        }
        .switch {
            display: inline-block;
            position: relative;
            width: 60px;
            height: 34px;
        }
        .switch input { 
            opacity: 0;
            width: 0;
            height: 0;
        }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .4s;
            border-radius: 34px;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 26px;
            width: 26px;
            left: 4px;
            bottom: 4px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }
        input:checked + .slider {
            background-color: #2196F3;
        }
        input:checked + .slider:before {
            transform: translateX(26px);
        }
        .value-container {
            margin-top: 20px;
            font-size: 24px;
        }
    </style>
</head>
<body>
    <h1>Control Panel</h1>
    <button id="button1" class="button">Button 1</button>
    <button id="button2" class="button">Button 2</button>
    <label class="switch">
        <input type="checkbox" id="switch1">
        <span class="slider"></span>
    </label>
    <label class="switch">
        <input type="checkbox" id="switch2">
        <span class="slider"></span>
    </label>
    <button id="runScriptButton" class="button">Run Script on Pi</button>
    <h1>Current Speed: <span id="currentSpeed">Loading...</span></h1>
    <h2>Image Path: <span id="path">Loading...</span></h2>
    <img id="image" alt="Speed Camera Image" /></p>
    <button onclick="updateSpeed()">Update Speed</button></p>
    <button onclick="nextRow()">Next Row</button>

    <script>
        let offset = 0;

        // Function to fetch and update the speed value
        function updateSpeed() {
            var xhr = new XMLHttpRequest();
            xhr.open('GET', 'getspeed.php?offset=' + offset, true);
            xhr.onload = function() {
                if (xhr.status === 200) {
                    console.log('Response Text:', xhr.responseText); // Log response
                    try {
                        var response = JSON.parse(xhr.responseText);
                        if (response.error) {
                            console.log('Error:', response.error);
                        } else {
                            document.getElementById('currentSpeed').textContent = response.ave_speed;
                            document.getElementById('path').textContent = response.image_path;
                            document.getElementById('image').src = response.image_path.replace(/\\/g, ''); // Update image source and remove escape characters
                        }
                    } catch (e) {
                        console.error('Error parsing JSON:', e);
                    }
                } else {
                    console.log('Failed to fetch speed. Status code:', xhr.status);
                }
            };
            xhr.onerror = function() {
                console.log('Request error');
            };
            xhr.send();
        }

        // Function to fetch the next row
        function nextRow() {
            offset++;
            updateSpeed();
        }
// AJAX call to execute a PHP script on the server
        document.getElementById('runScriptButton').onclick = function() {
            
            var xhr = new XMLHttpRequest();
            xhr.open('GET', 'runscript.php', true);
            xhr.onload = function() {
                if (xhr.status === 200) {
                    alert('Script executed successfully.');
                } else {
                    alert('Failed to execute script. Status code: ' + xhr.status);
                }
            };
            xhr.send();
        };

        // Update speed value initially
        updateSpeed();

        // Update speed value every 5 seconds (for example)
        setInterval(updateSpeed, 5000); // Adjust interval as needed
    </script>
    <script>
    fetch('getspeed.php?offset=0')
    .then(response => response.json())
    .then(data => {
        console.log('Response Text:', xhr.responseText); // Log response
                    try {
                        var response = JSON.parse(xhr.responseText);
                        if (response.error) {
                            console.log('Error:', response.error);
                        } else {
                            document.getElementById('currentSpeed').textContent = response.ave_speed;
                            document.getElementById('path').textContent = response.image_path;
                            document.getElementById('image').src = response.image_path.replace(/\\/g, ''); // Update image source and remove escape characters
                        }
                    })
    .catch(error => {
        console.error('Error fetching data:', error);
    });</script>
    
</body>
</html>
