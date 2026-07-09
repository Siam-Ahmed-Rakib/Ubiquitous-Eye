# Backend Integration Guide

The frontend sends satellite map images to the backend for analysis of:
- **Deforestation** - Forest cover loss detection
- **Land Encroachment** - Unauthorized settlement detection
- **River Erosion** - Riverbank change detection

## API Endpoint

### POST `/api/analyze`

**Request Format:**
- **Content-Type:** `multipart/form-data`

**Request Body Parameters:**
```
image: File (PNG image blob)
analysisType: String - one of:
  - "deforestation"
  - "land_encroachment"  
  - "river_erosion"
bounds: JSON string
  {
    "north": 26.5,
    "south": 23.2,
    "east": 92.0,
    "west": 88.5
  }
zoom: Number (7-19)
```

**Response Format:**
```json
{
  "status": "success",
  "data": {
    "coverage_percentage": 45.2,
    "severity": "high",
    "area_affected_sqkm": 123.45,
    "change_rate": 2.3,
    "recommendation": "Immediate intervention required",
    "confidence": 0.94,
    "additional_info": "..."
  }
}
```

## Example Response Fields

### For Deforestation:
- `coverage_percentage`: % of detected forest loss
- `severity`: "low" | "medium" | "high" | "critical"
- `area_affected_sqkm`: Square kilometers affected
- `change_rate`: Rate of change (percentage per year)

### For Land Encroachment:
- `coverage_percentage`: % of encroached area
- `severity`: "low" | "medium" | "high"
- `area_affected_sqkm`: Square kilometers encroached
- `settlement_count`: Number of detected settlements

### For River Erosion:
- `coverage_percentage`: % of riverbank affected
- `severity`: "low" | "medium" | "high"
- `erosion_width_meters`: Average width of erosion
- `migration_direction`: Direction of river migration

## Java Backend Example

```java
@PostMapping("/api/analyze")
public ResponseEntity<?> analyzeImage(
    @RequestParam("image") MultipartFile image,
    @RequestParam("analysisType") String analysisType,
    @RequestParam("bounds") String bounds,
    @RequestParam("zoom") int zoom) throws Exception {
    
    // Read image
    BufferedImage satellite = ImageIO.read(image.getInputStream());
    
    // Parse bounds
    JsonObject boundsJson = JsonParser.parseString(bounds).getAsJsonObject();
    
    // Run analysis based on analysisType
    Map<String, Object> result = AnalysisService.analyze(
        satellite, 
        analysisType, 
        boundsJson, 
        zoom
    );
    
    return ResponseEntity.ok(Map.of(
        "status", "success",
        "data", result
    ));
}
```

## Python Flask Example

```python
from flask import Flask, request, jsonify
import cv2
import numpy as np

@app.route('/api/analyze', methods=['POST'])
def analyze():
    image_file = request.files['image']
    analysis_type = request.form['analysisType']
    bounds = json.loads(request.form['bounds'])
    zoom = int(request.form['zoom'])
    
    # Read image
    image = cv2.imdecode(
        np.frombuffer(image_file.read(), np.uint8), 
        cv2.IMREAD_COLOR
    )
    
    # Run analysis
    result = analyze_image(image, analysis_type, bounds, zoom)
    
    return jsonify({
        "status": "success",
        "data": result
    })
```

## Python FastAPI Example

```python
from fastapi import FastAPI, UploadFile, Form
import cv2
import numpy as np

@app.post("/api/analyze")
async def analyze(
    image: UploadFile,
    analysisType: str,
    bounds: str,
    zoom: int
):
    contents = await image.read()
    img_array = np.frombuffer(contents, np.uint8)
    image_cv = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    bounds_dict = json.loads(bounds)
    
    result = analyze_image(image_cv, analysisType, bounds_dict, zoom)
    
    return {
        "status": "success",
        "data": result
    }
```

## Node.js Express Example

```javascript
const express = require('express');
const multer = require('multer');
const cv = require('opencv4nodejs');

app.post('/api/analyze', multer().single('image'), async (req, res) => {
    try {
        // Read image
        const imageBuffer = req.file.buffer;
        const mat = cv.imdecode(imageBuffer, cv.IMREAD_COLOR);
        
        const { analysisType, bounds, zoom } = req.body;
        const boundsObj = JSON.parse(bounds);
        
        // Run analysis
        const result = await analyzeImage(
            mat, 
            analysisType, 
            boundsObj, 
            zoom
        );
        
        return res.json({
            status: 'success',
            data: result
        });
    } catch (error) {
        res.status(500).json({
            status: 'error',
            message: error.message
        });
    }
});
```

## Frontend Configuration

Update the `.env` file:
```
VITE_BACKEND_URL=http://localhost:5000
```

Or for production:
```
VITE_BACKEND_URL=https://api.yourdomain.com
```

## Testing the Integration

1. Start the frontend dev server:
   ```bash
   npm run dev
   ```

2. Start your backend server on port 5000

3. On the map:
   - Select an analysis type
   - Click "Draw Rectangle" and draw on the map
   - Click "Send to Backend"
   - Check browser console for logs
   - Results should appear below the preview image

## Error Handling

The frontend automatically handles:
- Network timeouts (30 seconds)
- HTTP errors (displays error code)
- Backend errors (shows error message from server)
- CORS issues (ensure backend allows cross-origin requests)

## CORS Configuration

Backend should include CORS headers:
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: POST
Access-Control-Allow-Headers: Content-Type
```

Or for origins:
```
Access-Control-Allow-Origin: http://localhost:5174
```
