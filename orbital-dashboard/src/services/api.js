import axios from "axios";

const API_URL = "http://localhost:8000/api/visualization/snapshot";

export const fetchSnapshot = async () => {
  try {
    const res = await axios.get(API_URL);
    return res.data;
  } catch (err) {
    console.error("API Error", err);
    return {
      satellites: [
        { id: "SAT1", x: 3, y: 0, z: 0 },
        { id: "SAT2", x: -3, y: 1, z: 1 }
      ],
      debris: [
        { x: 2, y: 1, z: 0 },
        { x: -2, y: -1, z: 1 }
      ]
    };
  }
};